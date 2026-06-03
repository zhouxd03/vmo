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
from . import continuity, error_policy, ffmpeg_util, image_gen, reference_set, video_gen

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
            "dialogue": s.get("dialogue", ""),
            "emotion": s.get("emotion") or s.get("mood") or "",
            # per-shot duration from pacing (5字/秒，按情绪调)，供视频生成使用
            "duration": s.get("duration"),
        })
    return tasks


def _video_dynamics(task: dict) -> str:
    """Motion/pacing directives that turn a static image prompt into a video
    prompt: camera movement, subject action, emotional pacing + duration, and a
    consistency guard. Deterministic (no LLM) so it works offline & spends no
    credits, while staying editable by the user in the worktable."""
    parts = []
    cam = (task.get("camera") or "").strip()
    if cam:
        parts.append(f"运镜与景别：{cam}")
    act = (task.get("action") or "").strip()
    if act:
        parts.append(f"主体动作：{act}")
    emo = (task.get("emotion") or "").strip()
    dur = task.get("duration")
    pace = []
    if emo:
        pace.append(f"{emo}情绪")
    try:
        if dur:
            pace.append(f"约{int(dur)}秒")
    except (TypeError, ValueError):
        pass
    if pace:
        parts.append("节奏：" + "，".join(pace))
    parts.append("镜头运动自然流畅、画面无跳帧；保持人物外形、服装与道具形制和参考图一致")
    return "；".join(parts)


def build_shot_prompts(project: dict, shot_nos: Optional[list] = None) -> dict:
    """Preview the per-shot generation prompts WITHOUT generating anything.

    Returns ``{shot_no: {"image": str, "video": str}}`` using the same engine
    logic as real generation (style + camera + resolved @/#/$ shot text), so the
    worktable can show editable, what-you-see-is-what-gets-generated prompts. The
    video variant augments the image scene with motion/pacing dynamics.
    """
    out: dict = {}
    for t in build_tasks_from_shots(project, shot_nos):
        img = t.get("prompt", "") or ""
        dyn = _video_dynamics(t)
        base = img.rstrip("。，、 ")
        vid = f"{base}。【镜头动态】{dyn}" if base else dyn
        out[t.get("shot_no", "")] = {"image": img, "video": vid}
    return out


def _shot_duration(task: dict, params: dict) -> int:
    """Per-shot duration (s): prefer the shot's pacing-derived value, else the
    batch default; always clamp to the 15s model ceiling."""
    d = task.get("duration") or params.get("duration", 10)
    try:
        d = int(d)
    except (TypeError, ValueError):
        d = 10
    return max(1, min(15, d))


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


def _parse_aspect(aspect_ratio: str) -> tuple[int, int]:
    try:
        w, h = (aspect_ratio or "16:9").split(":")
        return int(w), int(h)
    except Exception:  # noqa: BLE001
        return 16, 9


def _normalize_refs_aspect(refs: list, aspect_ratio: str) -> list:
    """Center-crop every reference image to the target video aspect ratio so the
    model never stretches a mismatched-aspect reference (square/3:2/asset图) —
    the root cause of scale jitter between continuity shots. The first-frame tail
    already matches the video aspect, so it is returned untouched (no-op crop)."""
    if not refs:
        return refs
    aw, ah = _parse_aspect(aspect_ratio)
    out = []
    for r in refs:
        try:
            out.append(ffmpeg_util.crop_b64_to_aspect(r, aw, ah))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[Refs] 参考图比例归一化跳过: {e}")
            out.append(r)
    return out


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
    aspect_ratio = params.get("aspect_ratio", "16:9")
    refs, prompt = _finalize_refs(task, _asset_ref_items(pid, task))
    refs = _normalize_refs_aspect(refs, aspect_ratio)
    out = video_gen.generate_video(
        prompt,
        model=params.get("model"),
        duration=_shot_duration(task, params),
        aspect_ratio=aspect_ratio,
        resolution=params.get("resolution", "720p"),
        ref_images_b64=refs,
        save_dir=_out_dir(pid, batch["id"]),
        filename_prefix=task.get("shot_no") or task["id"],
        generate_audio=bool(params.get("generate_audio", False)),
        watermark=bool(params.get("watermark", False)),
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

    aspect_ratio = params.get("aspect_ratio", "16:9")

    # 连续性承接走「多模态软参考」(reference_image_urls)：上一镜尾帧作为参考图喂入，
    # 与导演图/站位图/资产图一并组装。本中转的 seed-2.0fast 不支持「首帧钉死」
    # 图生视频（实测输入图被静默丢弃 → 退化成文生视频），故沿用软参考方案。
    # 仍保留画幅居中裁剪（_normalize_refs_aspect），避免参考图比例不符被模型拉伸。
    if decision.get("use_tail_frame"):
        tail_b64 = continuity._read_b64(pid, prev_state.get("tail_frame"))
        if not tail_b64:
            # 中继种子缺失（上一镜失败 / 尾帧未抽出）→ 兜底切镜头直生，
            # 不强行承接不存在的尾帧，避免连续性错乱。
            decision = {
                **decision, "use_tail_frame": False, "action": "scene_cut",
                "fallback": "tail_missing",
                "reason": f"上一镜无可用尾帧（失败/缺失）→ 兜底切镜头直生｜原判: {decision.get('reason', '')}",
            }
            logger.warning(f"[Continuity] {shot_no} 中继种子缺失，兜底切镜头直生")
    # persist the (possibly downgraded) decision so the UI badge reflects the fallback
    story_state.set_decision(pid, shot_no, decision)
    batches.update_task(pid, batch["id"], task["id"], {"decision": decision})

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
    # 把衔接决策的运镜/景别提示注入提示词：默认走切镜头/景别切换衔接（见 continuity）
    hint = decision.get("camera_hint")
    if hint:
        prompt = f"【镜头衔接】{hint}\n{prompt}"
    refs = _normalize_refs_aspect(refs, aspect_ratio)
    out = video_gen.generate_video(
        prompt,
        model=params.get("model"),
        duration=_shot_duration(task, params),
        aspect_ratio=aspect_ratio,
        resolution=params.get("resolution", "720p"),
        ref_images_b64=refs,
        save_dir=_out_dir(pid, batch["id"]),
        filename_prefix=shot_no,
        generate_audio=bool(params.get("generate_audio", False)),
        watermark=bool(params.get("watermark", False)),
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
    """Run one task with error-type-aware retries.

    Retries are gated by ``error_policy.classify``: transient errors (network /
    5xx / rate-limit) are retried with progressive backoff up to max_attempts;
    content/credential/param errors are NOT retried (retrying can't succeed and
    only burns paid quota). A *fatal* error (bad key / no quota) additionally
    pauses the batch so the remaining shots are skipped instead of all failing.
    """
    bid = batch["id"]
    label = task.get("shot_no") or task["id"]
    if is_paused(bid):
        return "paused"
    batches.update_task(pid, bid, task["id"], {"status": "running"})
    attempts = task.get("attempts", 0)
    max_attempts = max(1, task.get("max_attempts", 3))
    err_info = {"category": "unknown", "message": "未执行", "retryable": False}
    while attempts < max_attempts:
        attempts += 1
        try:
            result = gen(pid, batch, task, params)
            batches.update_task(pid, bid, task["id"],
                                {"status": "done", "result": result, "attempts": attempts, "error": None})
            return "done"
        except Exception as e:  # noqa: BLE001
            err_info = error_policy.classify(e)
            logger.warning(
                f"[Batch {bid}] 任务 {label} 第{attempts}次失败 "
                f"[{err_info['category']}{('/' + err_info['code']) if err_info['code'] else ''}]: {err_info['message']}")
            if err_info["retryable"] and attempts < max_attempts:
                time.sleep(backoff * attempts)  # progressive backoff
                continue
            break  # non-retryable → stop immediately (don't waste quota)
    # record the structured error and the number of attempts actually spent
    err_info = {**err_info, "attempts_used": attempts}
    batches.update_task(pid, bid, task["id"],
                        {"status": "error", "error": err_info, "attempts": attempts})
    if err_info.get("abort_batch"):
        pause(bid)  # skip the remaining shots instead of failing each the same way
        logger.warning(f"[Batch {bid}] 致命错误，已中止后续生成: {err_info['message']}")
        return "fatal"
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
    aborted = False
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(_run_one, pid, batch, t, gen, params, backoff): t for t in pending}
        for fut in as_completed(futures):
            outcome = fut.result()
            if outcome in ("done", "error", "fatal"):
                done_count += 1
                report()
            if outcome == "fatal":
                aborted = True

    final = batches.get_batch(pid, bid)
    # when aborted by a fatal error, mark the still-pending shots as skipped so
    # the UI explains why they didn't run (rather than leaving them "pending")
    if aborted:
        for t in final["tasks"]:
            if t["status"] == "pending":
                batches.update_task(pid, bid, t["id"], {"status": "error", "error": {
                    "category": "skipped", "message": "[已跳过]｜前序致命错误（凭据/额度）中止本批，未消耗调用",
                    "retryable": False}})
        final = batches.get_batch(pid, bid)
    statuses = [t["status"] for t in final["tasks"]]
    if aborted:
        status = "error"
    elif is_paused(bid) and any(s == "pending" for s in statuses):
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
