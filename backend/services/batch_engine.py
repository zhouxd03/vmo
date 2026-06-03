"""Batch generation engine (Phase 4).

Runs a batch of image/video tasks with:
  - bounded concurrency (ThreadPoolExecutor sized by batch.concurrency)
  - per-task retry with backoff (up to task.max_attempts)
  - resume (tasks already "done" are skipped on re-run)
  - cooperative pause (a flag checked before each task starts)

Generation itself is delegated to the image/video services. The generator is
pluggable so the engine mechanics can be tested without real API credentials.
"""

import base64
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

from ..core import assets as assets_core
from ..core import batches
from ..core import settings as settings_store
from ..core import story_state
from ..core.paths import OUTPUT_DIR, PROJECTS_DIR
from . import continuity, image_gen, reference_set, video_gen

logger = logging.getLogger("batch_studio")

_pause_flags: dict[str, bool] = {}
_lock = threading.Lock()


def pause(bid: str) -> None:
    with _lock:
        _pause_flags[bid] = True


def _clear_pause(bid: str) -> None:
    with _lock:
        _pause_flags.pop(bid, None)


def is_paused(bid: str) -> bool:
    with _lock:
        return _pause_flags.get(bid, False)


# ── task building from shots ────────────────────────────────────────────────
def _shot_text(shot: dict) -> str:
    parts = [
        shot.get("scene", ""), shot.get("action", ""),
        " ".join(shot.get("characters", []) or []),
        " ".join(shot.get("props", []) or []),
        shot.get("key_elements", ""), shot.get("dialogue", ""),
    ]
    return "，".join(p for p in parts if p)


def build_tasks_from_shots(project: dict, shot_nos: Optional[list] = None) -> list:
    """Turn decomposed shots into batch tasks, auto-resolving @/#/$ materials."""
    shots = project.get("shots", []) or []
    if shot_nos:
        wanted = set(shot_nos)
        shots = [s for s in shots if s.get("shot_no") in wanted]
    asset_list = project.get("assets", []) or []
    bible = project.get("story_bible") or {}
    style = bible.get("style") or ""
    tasks = []
    for s in shots:
        text = _shot_text(s)
        res = assets_core.resolve_mentions(text, asset_list)
        prompt_parts = [p for p in (style, s.get("camera", ""), res["text"]) if p]
        tasks.append({
            "shot_no": s.get("shot_no", ""),
            "seq": s.get("seq"),
            "prompt": "，".join(prompt_parts),
            "materials": res["materials"],
            # carry raw shot fields for the continuity engine (Phase 5)
            "scene": s.get("scene", ""),
            "characters": s.get("characters", []),
            "props": s.get("props", []),
            "action": s.get("action", ""),
            "camera": s.get("camera", ""),
            "handoff": s.get("handoff", ""),
        })
    return tasks


# ── reference-image assembly (asset materials → priority-capped base64) ──────
def _max_refs() -> int:
    try:
        return int(settings_store.load_settings().get("max_reference_images", 8))
    except (TypeError, ValueError):
        return 8


def _asset_ref_items(pid: str, task: dict) -> list:
    """Reference items for a task's @/#/$ asset materials (those with a ref image)."""
    items = []
    for m in task.get("materials", []) or []:
        fn = m.get("ref_image")
        if not fn:
            continue
        path = PROJECTS_DIR / pid / "assets" / fn
        if not path.exists():
            continue
        items.append({
            "b64": base64.b64encode(path.read_bytes()).decode("utf-8"),
            "role": reference_set.material_role(m),
            "name": m.get("name"),
            "asset_id": m.get("asset_id"),
            "old_ph": m.get("placeholder"),
        })
    return items


def _finalize_refs(task: dict, items: list) -> tuple[list, str]:
    """Cap *items* by priority and build the labeled prompt. Returns (b64s, prompt)."""
    kept = reference_set.cap(items, _max_refs())
    prompt = reference_set.compose_prompt(task.get("prompt", ""), task.get("materials", []), kept)
    return [it["b64"] for it in kept], prompt


def _out_dir(pid: str, bid: str):
    d = OUTPUT_DIR / pid / bid
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── generators (real) ───────────────────────────────────────────────────────
def _gen_image_task(pid, batch, task, params):
    refs, prompt = _finalize_refs(task, _asset_ref_items(pid, task))
    out = image_gen.generate_image(
        prompt,
        model=params.get("model"),
        size=params.get("size", "1024x1024"),
        ref_images_b64=refs,
        save_dir=_out_dir(pid, batch["id"]),
        filename_prefix=task.get("shot_no") or task["id"],
    )
    return {"filename": out["filename"], "filepath": out["filepath"]}


def _gen_video_task(pid, batch, task, params):
    refs, prompt = _finalize_refs(task, _asset_ref_items(pid, task))
    out = video_gen.generate_video(
        prompt,
        model=params.get("model"),
        duration=int(params.get("duration", 10)),
        aspect_ratio=params.get("aspect_ratio", "16:9"),
        resolution=params.get("resolution", "720p"),
        ref_images_b64=refs,
        save_dir=_out_dir(pid, batch["id"]),
        filename_prefix=task.get("shot_no") or task["id"],
    )
    return {"filename": out["filename"], "filepath": out["filepath"]}


def _gen_video_task_continuity(pid, batch, task, params):
    """Video generation with cross-shot continuity (Phase 5).

    Runs only in sequential batches (concurrency forced to 1) so the previous
    shot's committed state — including its extracted tail frame — is available
    when deciding/handing off to this shot.
    """
    shot_no = task.get("shot_no") or task["id"]
    shot = {
        "shot_no": shot_no,
        "scene": task.get("scene", ""),
        "characters": task.get("characters", []),
        "props": task.get("props", []),
        "action": task.get("action", "") or task.get("prompt", ""),
        "camera": task.get("camera", ""),
        "handoff": task.get("handoff", ""),
    }
    prev_state = story_state.get_current(pid)
    style = params.get("style", "")
    use_llm = bool(params.get("decision_llm"))
    decision = continuity.decide_handoff(shot, prev_state, use_llm=use_llm,
                                         model=params.get("llm_model"))
    story_state.set_decision(pid, shot_no, decision)
    batches.update_task(pid, batch["id"], task["id"], {"decision": decision})

    # assemble references: continuity-generated images (尾帧→首帧 / 导演图 / 站位图)
    # are referenced explicitly and labeled, then asset materials, then capped
    # by priority so the total never exceeds settings.max_reference_images.
    items: list = []
    if decision.get("use_tail_frame"):
        tail_b64 = continuity._read_b64(pid, prev_state.get("tail_frame"))
        if tail_b64:
            items.append({"b64": tail_b64, "role": "first_frame", "name": "上一镜尾帧"})
    snap = story_state.get_shot_state(pid, shot_no) or {}
    director_b64 = continuity._read_b64(pid, snap.get("director_board"))
    if director_b64:
        items.append({"b64": director_b64, "role": "director", "name": "本镜导演图"})
    staging_b64 = continuity._read_b64(pid, snap.get("staging_image"))
    if staging_b64:
        items.append({"b64": staging_b64, "role": "staging", "name": "本镜站位图"})
    items.extend(_asset_ref_items(pid, task))
    refs, prompt = _finalize_refs(task, items)

    out = video_gen.generate_video(
        prompt,
        model=params.get("model"),
        duration=int(params.get("duration", 10)),
        aspect_ratio=params.get("aspect_ratio", "16:9"),
        resolution=params.get("resolution", "720p"),
        ref_images_b64=refs,
        save_dir=_out_dir(pid, batch["id"]),
        filename_prefix=shot_no,
    )

    # commit this shot's state, extract its tail frame for the next shot
    state_patch = continuity.shot_to_state(shot)
    story_state.commit_shot(pid, shot_no, state_patch)
    tail_info = None
    if out.get("filepath"):
        try:
            tail_info = continuity.extract_tail_frame(pid, shot_no, out["filepath"])
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[Continuity] {shot_no} 尾帧抽取失败: {e}")

    # auto mode: AI continuity review gate (best-effort; surfaced on the task)
    review = None
    if params.get("mode") == "auto" and params.get("ai_review"):
        try:
            review = continuity.ai_review(pid, shot, prev_state, model=params.get("llm_model"))
        except Exception as e:  # noqa: BLE001
            review = {"pass": None, "error": str(e), "source": "unavailable"}
            story_state.set_review(pid, shot_no, review)

    result = {"filename": out["filename"], "filepath": out["filepath"], "decision": decision}
    if tail_info:
        result["tail_frame"] = tail_info["tail_frame"]
    if review is not None:
        result["review"] = review
    return result


_GENERATORS: dict[str, Callable] = {"image": _gen_image_task, "video": _gen_video_task}


# ── runner ──────────────────────────────────────────────────────────────────
def _run_one(pid, batch, task, gen, params, backoff):
    bid = batch["id"]
    if is_paused(bid):
        return "paused"
    batches.update_task(pid, bid, task["id"], {"status": "running"})
    attempts = task.get("attempts", 0)
    max_attempts = max(1, task.get("max_attempts", 3))
    last_err = "未执行"
    while attempts < max_attempts:
        attempts += 1
        try:
            result = gen(pid, batch, task, params)
            batches.update_task(pid, bid, task["id"],
                                {"status": "done", "result": result, "attempts": attempts, "error": None})
            return "done"
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            logger.warning(f"[Batch {bid}] 任务 {task.get('shot_no') or task['id']} 第{attempts}次失败: {last_err}")
            if attempts < max_attempts:
                time.sleep(backoff)
    batches.update_task(pid, bid, task["id"],
                        {"status": "error", "error": last_err, "attempts": attempts})
    return "error"


def run_batch(pid: str, bid: str, *, on_progress: Optional[Callable[[int, int], None]] = None,
              generator: Optional[Callable] = None, backoff: float = 1.5) -> dict:
    batch = batches.get_batch(pid, bid)
    if not batch:
        raise ValueError("批次不存在")
    _clear_pause(bid)
    batches.set_status(pid, bid, "running")
    params = batch.get("params", {})
    continuity_on = batch["kind"] == "video" and bool(params.get("continuity"))
    if generator:
        gen = generator
    elif continuity_on:
        gen = _gen_video_task_continuity
    else:
        gen = _GENERATORS[batch["kind"]]
    # continuity needs sequential execution so tail frames chain in shot order
    concurrency = 1 if continuity_on else max(1, int(batch.get("concurrency", 2)))

    all_tasks = batch["tasks"]
    total = len(all_tasks)
    pending = [t for t in all_tasks if t["status"] != "done"]  # resume
    done_count = total - len(pending)

    def report():
        if on_progress:
            on_progress(done_count, total)

    report()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(_run_one, pid, batch, t, gen, params, backoff): t for t in pending}
        for fut in as_completed(futures):
            outcome = fut.result()
            if outcome in ("done", "error"):
                done_count += 1
                report()

    final = batches.get_batch(pid, bid)
    statuses = [t["status"] for t in final["tasks"]]
    if is_paused(bid) and any(s == "pending" for s in statuses):
        status = "paused"
    elif any(s == "error" for s in statuses):
        status = "error"
    elif all(s == "done" for s in statuses):
        status = "done"
    else:
        status = "paused"
    batches.set_status(pid, bid, status)
    _clear_pause(bid)
    return {
        "status": status,
        "total": total,
        "done": sum(1 for s in statuses if s == "done"),
        "error": sum(1 for s in statuses if s == "error"),
    }
