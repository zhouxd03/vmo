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
import hashlib
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

from ..core import assets as assets_core
from ..core import batches
from ..core import prompt_templates
from ..core import settings as settings_store
from ..core import story_state
from ..core.paths import OUTPUT_DIR, PROJECTS_DIR
from . import continuity, error_policy, ffmpeg_util, image_gen, reference_set, video_gen
from . import doubao_pool
from . import llm

logger = logging.getLogger("batch_studio")

_pause_flags: dict[str, bool] = {}
_lock = threading.Lock()
_global_slots_lock = threading.Lock()
_global_slots: threading.BoundedSemaphore | None = None
_global_slots_size = 0


class BatchPaused(Exception):
    """Internal cooperative stop signal for an in-flight batch task."""


def _global_limit() -> int:
    try:
        return max(1, min(16, int(settings_store.load_settings().get("max_concurrency", 3) or 3)))
    except Exception:  # noqa: BLE001
        return 3


def _global_generation_slots() -> threading.BoundedSemaphore:
    global _global_slots, _global_slots_size
    limit = _global_limit()
    with _global_slots_lock:
        if _global_slots is None or _global_slots_size != limit:
            _global_slots = threading.BoundedSemaphore(limit)
            _global_slots_size = limit
        return _global_slots


def _continuity_lock(pid: str) -> threading.RLock:
    return story_state.continuity_lock(pid)


def pause(bid: str) -> None:
    with _lock:
        _pause_flags[bid] = True


def _clear_pause(bid: str) -> None:
    with _lock:
        _pause_flags.pop(bid, None)


def is_paused(bid: str) -> bool:
    with _lock:
        return _pause_flags.get(bid, False)


def cancel_doubao_tasks(pid: str, bid: str, reason: str = "用户已停止") -> int:
    batch = batches.get_batch(pid, bid)
    if not batch:
        return 0
    count = 0
    for task in batch.get("tasks", []):
        task_ids = []
        if task.get("doubao_task_id"):
            task_ids.append(str(task.get("doubao_task_id")))
        preflight = task.get("reference_preflight") or {}
        if preflight.get("transport") == "doubao-pool" and preflight.get("video_task_id"):
            task_ids.append(str(preflight.get("video_task_id")))
        for task_id in set(task_ids):
            try:
                if doubao_pool.cancel_task(task_id, reason):
                    count += 1
            except Exception as e:  # noqa: BLE001
                logger.warning("[Batch %s] cancel doubao task %s failed: %s", bid, task_id, e)
    return count


# Task building from shots
def _shot_text(shot: dict) -> str:
    parts = [
        shot.get("scene", ""), shot.get("action", ""),
        " ".join(shot.get("characters", []) or []),
        " ".join(shot.get("props", []) or []),
        shot.get("key_elements", ""), shot.get("dialogue", ""),
    ]
    return "，".join(p for p in parts if p)


def _image_text(shot: dict) -> str:
    """Default still-frame prompt body. Dialogue belongs to video dynamics, not
    the image prompt; keeping it out prevents spoken lines from being rendered
    as visual text or repeated before the motion block."""
    parts = [
        shot.get("scene", ""),
        " ".join(shot.get("characters", []) or []),
        " ".join(shot.get("props", []) or []),
        shot.get("key_elements", ""),
        shot.get("action", ""),
        shot.get("emotion") or shot.get("mood") or "",
    ]
    return "，".join(p for p in parts if p)


def _project_pid(project: dict) -> Optional[str]:
    return project.get("_pid") or project.get("id")


def _decision_bridge_data(pid: Optional[str], shot_no) -> dict:
    if not pid or not shot_no:
        return {}
    snap = story_state.get_shot_state(pid, str(shot_no)) or {}
    decision = snap.get("decision") or {}
    if isinstance(decision.get("bridge_data"), dict):
        return decision["bridge_data"]
    return {}


def _decision_bridge_context(pid: Optional[str], shot_no, *, purpose: str = "video") -> str:
    data = _decision_bridge_data(pid, shot_no)
    if data:
        return continuity.render_bridge_context(data, purpose=purpose).strip()
    if not pid or not shot_no:
        return ""
    snap = story_state.get_shot_state(pid, str(shot_no)) or {}
    decision = snap.get("decision") or {}
    return (decision.get("bridge_context") or "").strip()


def _compact_bridge_context(text: str, *, max_lines: int = 4) -> str:
    """Keep only actionable bridge lines so prompts do not become analysis dumps."""
    text = (text or "").strip()
    if not text:
        return ""
    keep_prefix = ("桥接策略：", "承接重点：", "镜头提示：", "本镜收尾变量：", "本镜交给下一镜的收尾变量：")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    picked = [ln for ln in lines if ln.startswith(keep_prefix)]
    if not picked:
        picked = lines[-max_lines:]
    return "；".join(picked[:max_lines])


def build_tasks_from_shots(project: dict, shot_nos: Optional[list] = None) -> list:
    """Turn decomposed shots into batch tasks, auto-resolving @/#/$ materials."""
    shots = project.get("shots", []) or []
    if shot_nos:
        wanted = set(shot_nos)
        shots = [s for s in shots if s.get("shot_no") in wanted]
    asset_list = project.get("assets", []) or []
    bible = project.get("story_bible") or {}
    style = bible.get("style") or ""
    pid = _project_pid(project)
    tasks = []
    for s in shots:
        text = _image_text(s)
        res = assets_core.resolve_mentions(text, asset_list)
        prompt_parts = [p for p in (style, s.get("camera", ""), res["text"]) if p]
        chars = s.get("characters", []) or []
        tasks.append({
            "shot_no": s.get("shot_no", ""),
            "seq": s.get("seq"),
            "src_text": s.get("src_text", ""),
            "prompt": "，".join(prompt_parts),
            "style": style,
            "materials": res["materials"],
            # carry raw shot fields for the continuity engine (Phase 5)
            "scene": s.get("scene", ""),
            "characters": chars,
            "props": s.get("props", []),
            "action": s.get("action", ""),
            "camera": s.get("camera", ""),
            "handoff": s.get("handoff", ""),
            "dialogue": s.get("dialogue", ""),
            "emotion": s.get("emotion") or s.get("mood") or "",
            # Per-shot duration from pacing, used by video generation.
            "duration": s.get("duration"),
            "prompt_overrides": s.get("prompt_overrides") if isinstance(s.get("prompt_overrides"), dict) else {},
            # Normalized @ reference display fields for video prompt dynamics.
            "scene_ref": _norm_refs(s.get("scene", ""), asset_list),
            "action_ref": _norm_refs(s.get("action", ""), asset_list),
            "handoff_ref": _norm_refs(s.get("handoff", ""), asset_list),
            "dialogue_ref": _norm_refs(s.get("dialogue", ""), asset_list),
            "characters_ref": [_norm_refs(c, asset_list) for c in chars],
            "_bridge_data": _decision_bridge_data(pid, s.get("shot_no")),
            "_bridge_context": _decision_bridge_context(pid, s.get("shot_no"), purpose="video"),
        })
    return tasks


# Registered assets are resolved to @name; unresolved #scene/$prop tags are
# also normalized to @name before entering image/video prompts.
_STRAY_TAG_RE = re.compile(r"[#$](?=[\w\u4e00-\u9fff])")


def _norm_refs(text: str, asset_list: list) -> str:
    if not text:
        return ""
    t = assets_core.resolve_mentions(text, asset_list).get("text", text)
    return _STRAY_TAG_RE.sub("@", t)


def _dialogue_text(task: dict) -> str:
    """Dialogue speaker labels are audio metadata, not image-reference anchors."""
    text = (task.get("dialogue_ref") or task.get("dialogue") or "").strip()
    if not text:
        return ""
    # Normalize older speaker forms while preserving the @speaker marker for
    # voice/lip-sync attribution. Do not resolve it as an image reference here.
    text = re.sub(r"(对白|对话)\s*@(?=[\w\u4e00-\u9fff])", r"\1-@", text)
    text = re.sub(r"(画外旁白|旁白|内心OS|吐槽)\s*@(?=[\w\u4e00-\u9fff])", r"\1-@", text)
    return text


_CONSISTENCY_GUARD = (
    "镜头运动自然流畅、画面无跳帧；保持人物外形、服装、场景与道具形制和参考图严格一致；"
    "物体保持刚性不变形/不融化，手与道具咬合自然，人物不随镜头平移滑步，重力方向真实；"
    "禁止多手多指/穿模；禁止字幕，禁止水印"
)

_LONG_TAKE_KW = ("长镜头", "一镜到底", "无缝", "不切", "连续运动")

# Four-beat pacing labels used by the fallback video prompt.
_BEAT_LABELS = {
    2: ("起势", "收束钩子"),
    3: ("起势", "核心动作", "收束钩子"),
    4: ("起势", "核心动作", "转折强化", "收束钩子"),
}
_BEAT_WEIGHTS = {
    2: (0.5, 0.5),
    3: (0.4, 0.35, 0.25),
    4: (0.3, 0.3, 0.2, 0.2),
}


def _ff_scene(task: dict) -> str:
    return (task.get("scene_ref") or task.get("scene") or "").strip()


def _ff_action(task: dict) -> str:
    return (task.get("action_ref") or task.get("action") or "").strip()


def _ff_chars(task: dict) -> str:
    cs = task.get("characters_ref") or task.get("characters") or []
    return "、".join(c for c in cs if c)


def _as_int(v) -> int:
    try:
        return int(v) if v else 0
    except (TypeError, ValueError):
        return 0


def _transition_of(task: dict) -> str:
    """Default to a cut/shot-size transition unless long-take words are present."""
    blob = f"{task.get('handoff','')} {task.get('camera','')}"
    if any(k in blob for k in _LONG_TAKE_KW):
        return "长镜头无缝衔接，运动、构图与光线从上一镜自然延续"
    return "与上一镜切镜头/景别切换衔接，承接其人物状态与站位"


def _first_frame_anchor(task: dict) -> str:
    """Only describe the starting still frame; action belongs in timeline."""
    chars = _ff_chars(task)
    scene = _ff_scene(task)
    lead = chars or "主体"
    bits = [lead, scene and f"位于{scene}"]
    return "，".join(b for b in bits if b)


def _split_clauses(text: str) -> list[str]:
    """Split action text into executable beats."""
    text = (text or "").strip()
    if not text:
        return []
    parts = [p.strip("，、；;：: ") for p in re.split(r"[。？！\.\!\?\n]+", text) if p.strip("，、；;：: ")]
    if len(parts) < 2 and len(text) > 24:
        parts = [p.strip() for p in re.split(r"[，,；;、]+", text) if p.strip()]
    return parts or [text]


def _eff_dur(task: dict) -> int:
    """Effective shot duration in seconds, from shot or settings."""
    d = _as_int(task.get("duration"))
    if d <= 0:
        try:
            d = int(settings_store.load_settings().get("shot_target_seconds", 10))
        except (TypeError, ValueError):
            d = 10
    return max(3, min(15, d))


def _action_timeline(task: dict) -> str:
    """Distribute action across the configured duration."""
    action = _ff_action(task).rstrip("。？！. ")
    if not action:
        return ""
    dur = _eff_dur(task)
    clauses = _split_clauses(action)
    if dur >= 6:
        n = 4 if dur >= 12 else (3 if dur >= 8 else 2)
        n = min(n, max(2, len(clauses)))
    else:
        n = 1
    if n <= 1:
        return f"0-{dur}秒（连续动作）：{action}"
    if len(clauses) < n:
        ho = (task.get("handoff_ref") or task.get("handoff") or "").strip()
        emo = (task.get("emotion") or "").strip()
        clauses = [
            f"{action}的起势，明确人物接触关系、重心方向和画面起幅",
            f"{action}持续推进，动作幅度、表情反应和镜头关注点逐步增强",
            (f"动作落到{ho}，形成下一镜接口" if ho else f"动作进入收束，保留{emo or '当前'}情绪的可见反应"),
            (f"最终定格在{ho}，为下一镜留下清晰状态" if ho else "最终以可见姿态停顿收束，避免突然跳切"),
        ][:n]
    base, extra = divmod(len(clauses), n)
    per = [base + (1 if i < extra else 0) for i in range(n)]
    bounds, acc = [0], 0.0
    for w in _BEAT_WEIGHTS[n]:
        acc += w * dur
        bounds.append(int(round(acc)))
    bounds[-1] = dur
    for i in range(1, len(bounds)):
        if bounds[i] <= bounds[i - 1]:
            bounds[i] = bounds[i - 1] + 1
    labels = _BEAT_LABELS[n]
    out, ci = [], 0
    for k in range(n):
        seg = clauses[ci:ci + per[k]]
        ci += per[k]
        out.append(f"{bounds[k]}-{bounds[k + 1]}秒（{labels[k]}）：{'；'.join(seg)}")
    return "；".join(out)


def _video_dynamics(task: dict) -> str:
    """Deterministic fallback video dynamics."""
    lines = []
    anchor = _first_frame_anchor(task)
    if anchor:
        lines.append(f"【首帧锚定·最高权重】{anchor}，定格为视频起幅静帧")
    cam = (task.get("camera") or "").strip()
    if cam:
        lines.append(f"【运镜/景别】{cam}")
    tl = _action_timeline(task)
    if tl:
        lines.append(f"【动作过程·时间轴】{tl}")
    dlg = _dialogue_text(task)
    if dlg:
        voice_note = "声画同步，不出字幕"
        if re.search(r"内心|心声|OS|os|吐槽", dlg):
            voice_note = "内心OS/吐槽音轨，不表现为嘴唇开合，不出字幕"
        elif re.search(r"旁白|画外", dlg):
            voice_note = "画外旁白音轨，不出字幕"
        lines.append(f"【台词/旁白】{dlg}（{voice_note}）")
    prev = (task.get("_prev_handoff") or "").strip()
    head = f"承接上一镜（{prev}）；{_transition_of(task)}" if prev else _transition_of(task)
    join = [f"【衔接】{head}"]
    bridge = _compact_bridge_context(task.get("_bridge_context") or "")
    if bridge:
        join.append(f"连续性桥接变量：{bridge}")
    ho = (task.get("handoff_ref") or task.get("handoff") or "").strip()
    if ho:
        join.append(f"本镜收尾：{ho}")
    lines.append("｜".join(join))
    emo = (task.get("emotion") or "").strip()
    dur = _eff_dur(task)
    pace = [f"总时长{dur}秒"]
    if emo:
        pace.append(f"{emo}情绪")
    pace.append("零删减、动作均匀铺满整条时长")
    lines.append("【节奏】" + "，".join(pace))
    lines.append("【防穿帮/一致性】" + _CONSISTENCY_GUARD)
    return "\n".join(lines)


def _strip_style_prefix(text: str, style: str) -> str:
    text = (text or "").strip()
    style = (style or "").strip()
    if not text or not style:
        return text.rstrip("。，、 ")
    for sep in ("，", ",", "、", "\n"):
        prefix = style + sep
        if text.startswith(prefix):
            return text[len(prefix):].strip().rstrip("。，、 ")
    if text == style:
        return ""
    return text.rstrip("。，、 ")


def _video_prompt_vars(task: dict, image_prompt: str,
                       dynamics_override: Optional[str] = None) -> dict:
    """Variables exposed to the editable video_prompt template."""
    dur = _eff_dur(task)
    style = (task.get("style") or "").strip()
    return {
        "style": style,
        "image_prompt": _strip_style_prefix(image_prompt, style),
        "dynamics": dynamics_override if dynamics_override is not None else _video_dynamics(task),
        "first_frame": _first_frame_anchor(task),
        "action_timeline": _action_timeline(task),
        "transition": _transition_of(task),
        "handoff": (task.get("handoff_ref") or task.get("handoff") or "").strip(),
        "camera": (task.get("camera") or "").strip(),
        "action": _ff_action(task),
        "emotion": (task.get("emotion") or "").strip(),
        "duration": dur,
        "dialogue": _dialogue_text(task),
        "scene": _ff_scene(task),
        "characters": _ff_chars(task),
        "props": "、".join(task.get("props", []) or []),
        "bridge_context": _compact_bridge_context(task.get("_bridge_context") or ""),
        "consistency": _CONSISTENCY_GUARD,
    }


def build_video_prompt(task: dict, image_prompt: str) -> str:
    """Render the active `video_prompt` template for one shot/task."""
    body = prompt_templates.get_template_body("video_prompt")
    vid = prompt_templates.render(body, _video_prompt_vars(task, image_prompt)).strip()
    return _clean_generated_prompt(vid) or (image_prompt or "")


def _compose_llm_video_prompt(task: dict, image_prompt: str, video_prompt: str) -> str:
    """Keep LLM inference aligned with the same ecosystem as default prompts:
    first-frame/image prompt carries style and visual anchoring; video prompt
    carries motion. This prevents successful LLM inference from dropping style
    or static composition just because it returned only a dynamics block."""
    image = _clean_generated_prompt(image_prompt or "")
    motion = _clean_generated_prompt(video_prompt or "")
    if not motion:
        return build_video_prompt(task, image)
    motion = re.sub(r"^\s*銆愰暅澶村姩鎬併€慭s*", "", motion).strip()
    body = prompt_templates.get_template_body("video_prompt")
    return _clean_generated_prompt(
        prompt_templates.render(body, _video_prompt_vars(task, image, dynamics_override=motion))
    )


_TIMELINE_LINE_RE = re.compile(r"(?m)^(?P<prefix>\s*【动作过程[·\s]*时间轴】)(?P<body>[^\n]*)")
_TIMELINE_SEGMENT_RE = re.compile(
    r"(?P<start>\d+(?:\.\d+)?)\s*[-–—~至到]\s*(?P<end>\d+(?:\.\d+)?)\s*秒"
    r"(?:\s*[（(](?P<label>[^）)]{1,24})[）)])?\s*[：:]?\s*(?P<text>.*?)(?="
    r"\s*(?:[；;]\s*)?\d+(?:\.\d+)?\s*[-–—~至到]\s*\d+(?:\.\d+)?\s*秒|$)"
)
_TIMELINE_SHOT_RE = re.compile(
    r"(?:^|[；;\n])\s*镜头(?P<num>\d+)\s*[：:]\s*(?P<body>.*?)(?=(?:[；;\n]\s*镜头\d+\s*[：:])|$)",
    re.S,
)
_SHOT_SECONDS_RE = re.compile(r"(?<!\d)(?P<sec>\d+(?:\.\d+)?)\s*(?:s|秒)")


def _extract_action_timeline(video_prompt: str) -> str:
    """Return only the body after the action-timeline heading."""
    m = _TIMELINE_LINE_RE.search(video_prompt or "")
    return (m.group("body") if m else "").strip()


def _replace_action_timeline(video_prompt: str, timeline: str) -> str:
    """Replace only the action-timeline body, preserving every other section."""
    timeline = (timeline or "").strip()
    if not video_prompt or not timeline:
        return video_prompt or ""
    if _TIMELINE_LINE_RE.search(video_prompt):
        return _TIMELINE_LINE_RE.sub(lambda m: f"{m.group('prefix')}{timeline}", video_prompt, count=1)
    return (video_prompt.rstrip() + f"\n【动作过程·时间轴】{timeline}").strip()


def _strip_timeline_heading(text: str) -> str:
    text = _clean_generated_prompt(text or "")
    text = re.sub(r"^\s*【动作过程[·\s]*时间轴】\s*", "", text).strip()
    return text.strip(" \n\r\t\"'`")


def _timeline_text_from_llm_raw(raw: str) -> str:
    """Recover the timeline field from strict JSON, fenced JSON, or near-JSON."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
        text = text.strip("`").strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return str(data.get("timeline") or data.get("action_timeline") or data.get("video") or "")
    except Exception:  # noqa: BLE001
        pass
    start = text.find("{")
    end = max(text.rfind("}"), text.rfind("]"))
    if start != -1 and end != -1 and end > start:
        try:
            data = json.loads(text[start:end + 1])
            if isinstance(data, dict):
                return str(data.get("timeline") or data.get("action_timeline") or data.get("video") or "")
        except Exception:  # noqa: BLE001
            pass
    m = re.search(
        r'["“”]?(?:timeline|action_timeline|video)["“”]?\s*[:：]\s*(?P<value>[\s\S]+)',
        text,
        re.I,
    )
    if m:
        value = m.group("value").strip()
        value = re.sub(r"^\s*[\"'“”]", "", value)
        value = re.sub(r"[\"'“”]?\s*[,，]?\s*[}\]]\s*$", "", value).strip()
        return value
    return text


def _timeline_stage_label(index: int, total: int, raw: str = "") -> str:
    if total <= 1:
        stage = "连续动作"
    elif index == 0:
        stage = "起势"
    elif index == total - 1:
        stage = "收束钩子"
    else:
        labels = _BEAT_LABELS.get(total)
        if labels and index < len(labels):
            stage = labels[index]
        elif index == total - 2 and total >= 4:
            stage = "转折强化"
        else:
            stage = "核心动作"
    raw = (raw or "").strip()
    if raw and 0 < index < total - 1:
        for candidate in ("核心动作", "转折强化"):
            if candidate in raw:
                stage = candidate
                break
    return f"镜头{index + 1}·{stage}"

def _scaled_bounds(duration: int, weights: list[float]) -> list[int]:
    total = sum(w for w in weights if w > 0) or 1.0
    bounds, acc = [0], 0.0
    for w in weights:
        acc += max(w, 0.0) / total * duration
        bounds.append(int(round(acc)))
    bounds[-1] = duration
    for i in range(1, len(bounds)):
        if bounds[i] <= bounds[i - 1]:
            bounds[i] = bounds[i - 1] + 1
    if bounds[-1] > duration:
        bounds[-1] = duration
        for i in range(len(bounds) - 2, 0, -1):
            if bounds[i] >= bounds[i + 1]:
                bounds[i] = max(bounds[i + 1] - 1, bounds[i - 1] + 1)
    return bounds


def _timeline_from_shot_style(text: str, duration: int) -> str:
    """Accept 10S-template style `镜头1：...2s ...` and add time ranges."""
    items = []
    for m in _TIMELINE_SHOT_RE.finditer(text or ""):
        body = re.sub(r"\s+", " ", (m.group("body") or "").strip(" ；;"))
        if not body:
            continue
        sec_m = _SHOT_SECONDS_RE.search(body)
        sec = float(sec_m.group("sec")) if sec_m else 0.0
        if sec_m:
            body = (body[:sec_m.start()] + body[sec_m.end():]).strip(" ，,、")
        items.append({"seconds": sec, "text": body})
    if not items:
        return ""
    items = items[:max(1, min(6, duration))]
    weights = [it["seconds"] if it["seconds"] > 0 else 1.0 for it in items]
    bounds = _scaled_bounds(duration, weights)
    out = []
    for i, it in enumerate(items):
        out.append(f"{bounds[i]}-{bounds[i + 1]}秒（{_timeline_stage_label(i, len(items))}）：{it['text']}")
    return "；".join(out)


def _normalize_timeline_text(text: str, duration: int, fallback: str = "") -> str:
    """Clamp LLM timeline segments to 0->duration while keeping segment wording."""
    text = _strip_timeline_heading(text)
    if not text:
        return fallback.strip()
    if _ENGINE_REF_TOKEN_RE.search(text):
        return fallback.strip()

    compact_text = text.replace("\n", "；")
    matches = list(_TIMELINE_SEGMENT_RE.finditer(compact_text))
    if not matches:
        shot_style = _timeline_from_shot_style(text, duration)
        if shot_style:
            return shot_style
        text = re.sub(r"\s+", " ", text).strip("；; ")
        return f"0-{duration}秒（连续动作）：{text}" if text else fallback.strip()

    segs: list[dict] = []
    for m in matches:
        body = re.sub(r"\s+", " ", (m.group("text") or "").strip(" ；;"))
        if not body:
            continue
        label = (m.group("label") or "").strip()
        segs.append({"label": label, "text": body})
    if not segs:
        return fallback.strip()

    segs = segs[:max(1, min(6, duration))]
    if duration == 10 and len(segs) == 5:
        bounds = [0, 2, 4, 6, 9, 10]
    elif duration == 10 and len(segs) >= 4:
        bounds = [0, 3, 6, 9, 10]
        segs = segs[:4]
    elif duration == 10 and len(segs) == 3:
        bounds = [0, 4, 8, 10]
    elif duration == 10 and len(segs) == 2:
        bounds = [0, 5, 10]
    else:
        n = max(1, min(6, len(segs)))
        weights = list(_BEAT_WEIGHTS.get(n) or ([1.0] * n))
        bounds = _scaled_bounds(duration, weights)
        segs = segs[:n]

    out = []
    for i, seg in enumerate(segs):
        label = _timeline_stage_label(i, len(segs), seg.get("label", ""))
        out.append(f"{bounds[i]}-{bounds[i + 1]}秒（{label}）：{seg['text']}")
    return "；".join(out)


_PROMPT_META_SECTION_RE = re.compile(
    r"\n*\s*【输出要求】[\s\S]*?(?=\n\s*【[^】]+】|\Z)",
    re.MULTILINE,
)
_ENGINE_REF_TOKEN_RE = re.compile(r"@图\d+|reference_image_urls", re.IGNORECASE)


def _clean_generated_prompt(text: str) -> str:
    """Remove prompt-engineering metadata that should not reach image/video APIs."""
    if not text:
        return ""
    cleaned = reference_set.strip_definition_block(str(text)).strip()
    cleaned = _PROMPT_META_SECTION_RE.sub("", cleaned).strip()
    # LLMs sometimes echo these as plain bullets even when no heading is present.
    cleaned = re.sub(r"(?m)^\s*[-•]\s*只写本镜真正需要的动态信息.*(?:\n|$)", "", cleaned)
    cleaned = re.sub(r"(?m)^\s*[-•]\s*不要为了炫技堆叠过多运镜术语.*(?:\n|$)", "", cleaned)
    return cleaned.strip()


def _continuity_ref_items(pid: Optional[str], task: dict,
                          with_b64: bool = True,
                          manual_decisions: Optional[dict] = None) -> list:
    if not pid or not task:
        return []
    shot_no = task.get("shot_no") or task.get("id")
    if not shot_no:
        return []
    manual = (manual_decisions or {}).get(str(shot_no)) or (manual_decisions or {}).get(shot_no)
    decision = manual if isinstance(manual, dict) else (story_state.get_shot_state(pid, shot_no) or {}).get("decision") or {}
    if not isinstance(decision, dict):
        decision = {}
    items: list = []
    current = story_state.get_shot_state(pid, shot_no) or {}
    prev_state = None
    prev_no = _previous_shot_no(shot_no)
    prev_state = story_state.get_shot_state(pid, prev_no) if prev_no else None
    cont_dir = story_state.continuity_dir(pid)

    def add(filename, role, name):
        if not filename:
            return
        path = cont_dir / filename
        if not path.exists():
            return
        item = {
            "role": role,
            "name": name,
            "filename": filename,
            "source": "continuity",
        }
        if with_b64:
            item["b64"] = base64.b64encode(path.read_bytes()).decode("utf-8")
        items.append(item)

    if decision.get("use_tail_frame") and prev_state:
        add(prev_state.get("tail_frame"), "first_frame", "上一镜尾帧")
    if decision.get("use_director_board"):
        add(current.get("director_board"), "director", "本镜导演图")
    if decision.get("use_staging"):
        add(current.get("staging_image"), "staging", "本镜站位图")
    return items


def _previous_shot_no(shot_no) -> str:
    s = str(shot_no or "")
    m = re.match(r"^(.*?C)(\d+)$", s)
    if m:
        n = int(m.group(2))
        if n > 1:
            return f"{m.group(1)}{n - 1:0{len(m.group(2))}d}"
    try:
        n = int(s)
        return str(n - 1) if n > 1 else ""
    except (TypeError, ValueError):
        return ""


def _has_continuity_visual(pid: Optional[str], task: dict) -> bool:
    decision = ((story_state.get_shot_state(pid, task.get("shot_no")) or {}).get("decision")
                if pid and task else None) or {}
    return bool(decision.get("use_staging") or decision.get("use_director_board"))


def _preview_ref_items(pid: Optional[str], task: dict,
                       with_b64: bool = False,
                       include_continuity: bool = False,
                       manual_decisions: Optional[dict] = None) -> list:
    items = _asset_ref_items(pid, task, with_b64=with_b64)
    if include_continuity:
        items += _continuity_ref_items(pid, task, with_b64=with_b64,
                                       manual_decisions=manual_decisions)
    return items


def _ref_preview_url(pid: Optional[str], item: dict) -> str:
    if not pid:
        return ""
    filename = item.get("filename")
    if item.get("source") == "continuity" and filename:
        return f"/api/projects/{pid}/continuity-image/{filename}"
    if item.get("source") == "asset" and filename:
        return f"/api/projects/{pid}/asset-image/{filename}"
    if item.get("asset_id"):
        try:
            asset = {a.get("id"): a for a in assets_core.list_assets(pid)}.get(item.get("asset_id")) or {}
            if asset.get("ref_image"):
                return f"/api/projects/{pid}/asset-image/{asset.get('ref_image')}"
        except Exception:  # noqa: BLE001
            return ""
    return ""


def _serialize_ref_items(pid: Optional[str], kept: list) -> list:
    out = []
    for i, item in enumerate(kept or [], start=1):
        out.append({
            "token": f"@图{i}",
            "role": item.get("role") or "",
            "name": item.get("name") or "",
            "asset_id": item.get("asset_id") or "",
            "filename": item.get("filename") or "",
            "source": item.get("source") or ("asset" if item.get("asset_id") else ""),
            "url": _ref_preview_url(pid, item),
            "voice": item.get("voice") or "",
        })
    return out


def _preview_refs(pid: Optional[str], task: dict,
                  kind: str = "video",
                  include_continuity: bool = False,
                  manual_decisions: Optional[dict] = None) -> tuple[list, str]:
    items = _preview_ref_items(pid, task, with_b64=False,
                               include_continuity=include_continuity,
                               manual_decisions=manual_decisions)
    kept = _stable_cap_items(items, _max_refs(), require_b64=False)
    if not kept:
        return [], ""
    return kept, reference_set.build_definition_block(kept, kind, require_b64=False)


def _preview_definition_block(pid: Optional[str], task: dict,
                              kind: str = "video",
                              include_continuity: bool = False,
                              manual_decisions: Optional[dict] = None) -> str:
    """Preview definition block using the same ref order as generation."""
    _kept, head = _preview_refs(pid, task, kind, include_continuity=include_continuity,
                                manual_decisions=manual_decisions)
    return head


def _with_preview_def(pid: Optional[str], task: dict, video_prompt: str,
                      kind: str = "video",
                      include_continuity: bool = False,
                      manual_decisions: Optional[dict] = None) -> str:
    """Prepend the preview definition block to *video_prompt* (idempotent)."""
    body = reference_set.strip_definition_block(video_prompt or "")
    head = _preview_definition_block(pid, task, kind,
                                     include_continuity=include_continuity,
                                     manual_decisions=manual_decisions)
    if not head:
        return body
    return head + "\n\n" + body if body else head


def build_shot_prompts(project: dict, shot_nos: Optional[list] = None,
                       *, include_saved: bool = True,
                       include_continuity_refs: bool = False,
                       manual_continuity: Optional[dict] = None) -> dict:
    """Preview per-shot generation prompts without generating media."""
    pid = project.get("_pid") or project.get("id")
    want = {str(n) for n in shot_nos} if shot_nos else None
    out: dict = {}
    prev_ho = ""
    for t in build_tasks_from_shots(project):
        no = t.get("shot_no", "")
        if prev_ho:
            t["_prev_handoff"] = prev_ho
        if want is None or str(no) in want:
            img = t.get("prompt", "") or ""
            saved = t.get("prompt_overrides") if include_saved and isinstance(t.get("prompt_overrides"), dict) else {}
            image = (saved.get("image") or img).strip()
            video = (saved.get("video") or build_video_prompt(t, image)).strip()
            kept, head = _preview_refs(pid, t, "video",
                                       include_continuity=include_continuity_refs,
                                       manual_decisions=manual_continuity)
            body = reference_set.strip_definition_block(video or "").strip()
            out[no] = {
                "image": image,
                "video": (head + "\n\n" + body if head and body else head or body),
                "refs": _serialize_ref_items(pid, kept),
                **({"source": saved.get("source")} if saved.get("source") else {}),
            }
        prev_ho = (t.get("handoff_ref") or t.get("handoff") or "").strip()
    return out


_ASSET_TYPE_LABEL = {"character": "角色", "scene": "场景", "prop": "道具"}


def _assets_desc_for_task(project: dict, task: dict) -> str:
    """Material context for per-shot prompt inference."""
    by_id = {a.get("id"): a for a in (project.get("assets") or [])}
    lines, seen = [], set()
    for m in task.get("materials", []) or []:
        aid = m.get("asset_id")
        if aid in seen:
            continue
        seen.add(aid)
        a = by_id.get(aid) or {}
        name = m.get("name") or a.get("name") or ""
        label = _ASSET_TYPE_LABEL.get(a.get("type") or m.get("type"), "资产")
        desc = (a.get("desc") or "").strip()
        appr = (a.get("appearance") or "").strip()
        detail = "；".join(x for x in (desc, appr and f"外观:{appr}") if x)
        lines.append(f"@{name}（{label}）：{detail or '（无说明）'}")
    return "\n".join(lines) or "（本镜未关联已登记资产）"


def infer_shot_prompt(project: dict, shot_no, *, model: Optional[str] = None) -> dict:
    """Ask an LLM to optimize only the action timeline for one shot."""
    started_at = time.time()
    pid = project.get("_pid") or project.get("id")
    target, prev_ho = None, ""
    for t in build_tasks_from_shots(project):
        if str(t.get("shot_no")) == str(shot_no):
            target = t
            break
        prev_ho = (t.get("handoff_ref") or t.get("handoff") or "").strip()
    if target is None:
        raise ValueError(f"分镜 {shot_no} 不存在")
    if prev_ho:
        target["_prev_handoff"] = prev_ho
    if not isinstance(target.get("_bridge_data"), dict) or not target.get("_bridge_data"):
        target["_bridge_data"] = _decision_bridge_data(pid, target.get("shot_no"))
    if not (target.get("_bridge_context") or "").strip():
        target["_bridge_context"] = _decision_bridge_context(pid, target.get("shot_no"), purpose="video")
    img_draft = target.get("prompt", "") or ""
    bible = project.get("story_bible") or {}
    fallback_image = img_draft
    # Read-only base prompt/context for AI timeline overlay. Never persist this
    # back into the fallback template or the dynamics variable source.
    fallback_dynamics = _video_dynamics(target)
    fallback_vid = build_video_prompt(target, fallback_image)
    fallback_timeline = (
        _extract_action_timeline(fallback_dynamics)
        or _extract_action_timeline(fallback_vid)
        or _action_timeline(target)
    )
    dur = _eff_dur(target)
    variables = {
        "style": (bible.get("style") or target.get("style") or "").strip(),
        "duration": str(dur),
        "shot_no": str(target.get("shot_no") or shot_no),
        "src_text": target.get("src_text") or "",
        "image_draft": fallback_image,
        "scene": _ff_scene(target),
        "characters": "、".join(target.get("characters_ref") or target.get("characters") or []),
        "props": "、".join(target.get("props", []) or []),
        "action": _ff_action(target),
        "emotion": (target.get("emotion") or "").strip(),
        "dialogue": _dialogue_text(target),
        "prev_handoff": prev_ho or "（本镜为首镜，无上文）",
        "bridge_context": (
            continuity.render_bridge_context(target.get("_bridge_data") or {}, purpose="infer").strip()
            or _compact_bridge_context(target.get("_bridge_context") or "")
        ),
        "handoff": (target.get("handoff_ref") or target.get("handoff") or "").strip(),
        "assets_desc": _assets_desc_for_task(project, target),
        "fallback_video": fallback_vid,
        "fallback_dynamics": fallback_dynamics,
        "fallback_timeline": fallback_timeline,
        "consistency": _CONSISTENCY_GUARD,
    }
    try:
        body = prompt_templates.get_preset_body("shot_prompt_infer", "ten_second_timeline")
        rendered_prompt = prompt_templates.render(body, variables)
        logger.info(
            "[InferTimeline] start shot=%s duration=%s prompt_chars=%s model=%s",
            shot_no, dur, len(rendered_prompt), model or "default",
        )
        raw = llm.chat(
            [{"role": "user", "content": rendered_prompt}],
            model=model, temperature=0.25, max_tokens=6000, response_json=True, timeout=300,
        )
        raw_timeline = _timeline_text_from_llm_raw(raw)
        timeline = _normalize_timeline_text(raw_timeline, dur, fallback_timeline)
        if not timeline:
            raise llm.LLMError("LLM 返回空时间轴")
        if _ENGINE_REF_TOKEN_RE.search(timeline):
            raise llm.LLMError("LLM 输出混入引擎内部参考图编号，请重新推理")
        if len(timeline) > 6000:
            raise llm.LLMError("LLM 返回时间轴异常过长，请检查模型输出")
        vid = _replace_action_timeline(fallback_vid, timeline)
        kept, head = _preview_refs(pid, target, "video", include_continuity=True)
        body = reference_set.strip_definition_block(vid or "").strip()
        logger.info(
            "[InferTimeline] done shot=%s elapsed=%.1fs timeline_chars=%s",
            shot_no, time.time() - started_at, len(timeline),
        )
        return {
            "image": fallback_image,
            "video": (head + "\n\n" + body if head and body else head or body),
            "refs": _serialize_ref_items(pid, kept),
            "source": "llm_timeline",
            "timeline": timeline,
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("infer_shot_prompt fallback (%s) elapsed=%.1fs: %s", shot_no, time.time() - started_at, e)
        kept, head = _preview_refs(pid, target, "video", include_continuity=True)
        body = reference_set.strip_definition_block(fallback_vid or "").strip()
        return {
            "image": fallback_image,
            "video": (head + "\n\n" + body if head and body else head or body),
            "refs": _serialize_ref_items(pid, kept),
            "source": "fallback",
            "error": str(e),
        }


def _shot_duration(task: dict, params: dict) -> int:
    """Per-shot duration (s), clamped to the 15s model ceiling.

    Seedance uses the video-generation control value directly because some
    channels only accept the selected fixed duration (for example 15s).
    """
    if _is_seedance_video_params(params):
        d = params.get("duration") or task.get("duration") or 10
    else:
        d = task.get("duration") or params.get("duration", 10)
    try:
        d = int(d)
    except (TypeError, ValueError):
        d = 10
    return max(1, min(15, d))


def _video_timeout(params: dict) -> int:
    raw = (
        params.get("timeout")
        or params.get("video_timeout")
        or params.get("poll_timeout")
        or settings_store.load_settings().get("video_timeout", 300)
        or 300
    )
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 300
    return max(60, min(3600, value))


def _video_poll_interval(params: dict) -> int:
    raw = params.get("poll_interval") or params.get("video_poll_interval") or 10
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 10
    return max(10, min(60, value))


# Reference-image assembly: asset materials to priority-capped base64
def _max_refs() -> int:
    try:
        return int(settings_store.load_settings().get("max_reference_images", 8))
    except (TypeError, ValueError):
        return 8


def _asset_ref_sort_key(task: dict, item: dict) -> tuple:
    """One canonical order for preview, prompt numbering, upload, and mapping.

    Do not use mention-detection order here. The only source of truth is the
    decomposed shot structure:
      characters[] order -> scene -> props[] order -> any other matched refs.
    Continuity refs are appended outside _asset_ref_items and keep that explicit
    decision order.
    """
    return reference_set.shot_sort_key(task, item)


def _asset_ref_items(pid: str, task: dict, with_b64: bool = True) -> list:
    """Reference items for a task's asset materials with ref images."""
    if not pid:
        return []
    asset_dir = PROJECTS_DIR / pid / "assets"
    by_id = {a.get("id"): a for a in assets_core.list_assets(pid)}
    items = []
    seen_assets = set()
    for m in task.get("materials", []) or []:
        fn = m.get("ref_image")
        if not fn:
            continue
        asset_key = m.get("asset_id") or fn
        if asset_key in seen_assets:
            continue
        seen_assets.add(asset_key)
        path = asset_dir / fn
        if not path.exists():
            continue
        it = {
            "role": reference_set.material_role(m),
            "name": m.get("name"),
            "asset_id": m.get("asset_id"),
            "old_ph": m.get("placeholder"),
            "filename": fn,
            "source": "asset",
        }
        asset = by_id.get(m.get("asset_id")) or {}
        if asset.get("voice"):
            it["voice"] = asset.get("voice")
        if with_b64:
            it["b64"] = base64.b64encode(path.read_bytes()).decode("utf-8")
        items.append(it)
    # Canonical numbering/upload order. Never rely on resolve_mentions' raw
    # detection order, which is optimized for matching safety, not generation.
    ordered = reference_set.sort_for_shot(items, task)
    logger.info(
        "[Refs] canonical asset order: %s",
        [f"{i + 1}:{it.get('role')}:{it.get('name')}" for i, it in enumerate(ordered)],
    )
    return ordered


def _stable_cap_items(items: list, max_n: int, *, require_b64: bool = True,
                      required_roles: set[str] | None = None) -> list:
    """Keep the incoming reference order stable so existing @图N numbers do not
    shift when continuity images are appended. Required continuity roles are
    preserved by evicting non-required tail items if the cap is reached."""
    required_roles = required_roles or set()
    pool = [it for it in (items or []) if (it.get("b64") or not require_b64)]
    if max_n is None or max_n <= 0 or len(pool) <= max_n:
        return pool
    kept = pool[:max_n]
    kept_required = {it.get("role") for it in kept if it.get("role") in required_roles}
    for item in pool[max_n:]:
        role = item.get("role")
        if role not in required_roles or role in kept_required:
            continue
        replace_at = next(
            (idx for idx in range(len(kept) - 1, -1, -1)
             if kept[idx].get("role") not in required_roles),
            None,
        )
        if replace_at is None:
            break
        kept[replace_at] = item
        kept_required.add(role)
    return kept


def _finalize_ref_items(task: dict, items: list, kind: str = "video", max_refs: int | None = None,
                        required_roles: set[str] | None = None) -> tuple[list, str]:
    """Cap *items* by priority and build the unified-@ reference prompt.
    Returns (kept_items, prompt)."""
    kept = _stable_cap_items(items, _max_refs() if max_refs is None else max_refs,
                             required_roles=required_roles)
    required_roles = required_roles or set()
    kept_roles = {it.get("role") for it in kept}
    missing_required = sorted(role for role in required_roles if role not in kept_roles)
    if missing_required:
        raise RuntimeError(
            f"请求构建失败：已选中的连续性参考图被总参考图上限裁掉，缺失={missing_required}；"
            "请调高上限或取消对应桥接选择，系统不会少图提交。"
        )
    if kind == "video":
        roles = [it.get("role") or "unknown" for it in kept]
        mapping = [f"@图{i + 1}:{it.get('role') or 'unknown'}:{it.get('name') or ''}"
                   for i, it in enumerate(kept)]
        logger.info(f"[Refs] 视频最终引用图 count={len(kept)} roles={roles} mapping={mapping}")
    prompt = reference_set.compose_prompt(
        task.get("prompt", ""), task.get("materials", []), kept, kind=kind)
    return kept, prompt


def _finalize_refs(task: dict, items: list, kind: str = "video", max_refs: int | None = None,
                   required_roles: set[str] | None = None) -> tuple[list, str]:
    """Cap *items* by priority and build the unified-@ reference prompt.
    Returns (b64s, prompt)."""
    kept, prompt = _finalize_ref_items(task, items, kind=kind, max_refs=max_refs,
                                       required_roles=required_roles)
    return [it["b64"] for it in kept], prompt


def _is_seedance_video_params(params: dict) -> bool:
    model = str((params or {}).get("model") or "").strip().lower()
    if model.startswith("doubao-seedance"):
        return True
    try:
        _base, _key, default_model, provider = video_gen._resolve_creds(None, None)
        return provider == "seedance" or str(default_model or "").lower().startswith("doubao-seedance")
    except Exception:  # noqa: BLE001
        return False


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _is_doubao_pool_video_params(params: dict) -> bool:
    params = params or {}
    model = str(params.get("model") or "").strip().lower()
    provider = str(params.get("provider") or params.get("video_provider") or "").strip().lower()
    base = str(params.get("base_url") or params.get("api_base") or "").strip().lower()
    if provider == "doubao_pool" or base.startswith("local://doubao-pool") or model == "doubao-web-video":
        return True
    try:
        resolved_base, _key, default_model, resolved_provider = video_gen._resolve_creds(
            params.get("base_url"), params.get("api_key"))
        return (
            resolved_provider == "doubao_pool"
            or str(resolved_base or "").lower().startswith("local://doubao-pool")
            or str(default_model or "").strip().lower() == "doubao-web-video"
        )
    except Exception:  # noqa: BLE001
        return False


def _doubao_pool_preflight_error(batch: dict, pending: list[dict]) -> Optional[dict]:
    if batch.get("kind") != "video" or not pending:
        return None
    if not _is_doubao_pool_video_params(batch.get("params") or {}):
        return None
    try:
        snapshot = doubao_pool.list_pool()
    except Exception as e:  # noqa: BLE001
        return {
            "category": "fatal",
            "code": "doubao_pool_preflight_failed",
            "retryable": False,
            "abort_batch": True,
            "message": f"[豆包号池不可用] 启动前检查号池失败：{e}",
            "hint": "请先打开左侧“豆包号池”确认账号、插件和本地浏览器状态正常，再重新开始批量生成。",
            "detail": str(e)[:500],
        }
    accounts = snapshot.get("accounts") or []
    enabled = [a for a in accounts if a.get("enabled", True)]
    available = _safe_int(snapshot.get("availableTotal"), 0)
    remaining = _safe_int(snapshot.get("remainingTotal"), 0)
    active = _safe_int(snapshot.get("activeTotal"), 0)
    if not accounts:
        reason = "本地号池还没有账号。"
    elif not enabled:
        reason = f"号池内有 {len(accounts)} 个账号，但都未启用。"
    elif available <= 0 and active > 0:
        reason = f"当前账号可用并发名额已被运行中任务占满（运行中 {active}，本地剩余次数 {remaining}）。"
    elif available <= 0:
        reason = f"账号存在但没有可用视频次数（本地剩余次数 {remaining}，运行中 {active}）。"
    else:
        return None
    account_bits = []
    for account in accounts[:4]:
        account_bits.append(
            f"{account.get('name') or account.get('id')}: "
            f"enabled={bool(account.get('enabled', True))}, "
            f"available={_safe_int(account.get('available'), 0)}, "
            f"remaining={_safe_int(account.get('remaining'), 0)}, "
            f"active={_safe_int(account.get('active'), 0)}, "
            f"remoteCredit={account.get('remoteCreditRemaining') if account.get('remoteCreditRemaining') not in (None, '') else '-'}, "
            f"health={account.get('accountHealthStatus') or 'ok'}"
        )
    detail = "; ".join(account_bits)
    return {
        "category": "fatal",
        "code": "doubao_pool_no_available_account",
        "retryable": False,
        "abort_batch": True,
        "message": f"[豆包号池不可用] {reason} 请到左侧“豆包号池”新增/启用账号，或重置账号可生产次数后再开始批量生成。",
        "hint": f"本次批量共有 {len(pending)} 个待生成任务；已在启动前停止，避免重复打开网页和刷出多条相同失败。账号状态：{detail or '无账号'}",
        "detail": detail[:500],
    }


_TAIL_NEGATION_PHRASES = (
    "无需尾帧", "不需要尾帧", "不要尾帧", "不使用尾帧", "不用尾帧",
    "取消尾帧", "禁止尾帧", "避免尾帧", "无需首尾帧", "不要首尾帧",
    "不需要首尾帧", "非长镜头", "切镜头", "景别切换",
)


def _tail_frame_negated(decision: dict, task: dict) -> bool:
    text = "\n".join(str(x or "") for x in (
        decision.get("camera_hint"),
        decision.get("reason"),
        decision.get("bridge_context"),
        continuity.render_bridge_context(decision.get("bridge_data") or {}, purpose="video"),
        task.get("prompt"),
    ))
    return any(phrase in text for phrase in _TAIL_NEGATION_PHRASES)


def _reconcile_tail_decision(decision: dict, task: dict) -> dict:
    if not decision.get("use_tail_frame") or not _tail_frame_negated(decision, task):
        return decision
    out = {
        **decision,
        "use_tail_frame": False,
        "long_take": False,
        "prefer_cut": True,
    }
    out["scene_cut"] = not (out.get("use_staging") or out.get("use_director_board"))
    if out.get("use_director_board"):
        out["strategy"] = "director_board"
    elif out.get("use_staging"):
        out["strategy"] = "staging"
    elif out.get("scene_cut"):
        out["strategy"] = "scene_cut"
    else:
        out["strategy"] = "cinematic_cut"
    reason = (out.get("reason") or "").strip()
    out["reason"] = (reason + "；" if reason else "") + "检测到提示词/衔接文本明确否定尾帧，已取消上一镜尾帧引用"
    logger.warning(
        "[Continuity] %s tail-frame conflict reconciled: prompt/hint negates tail frame, use_tail_frame forced to false",
        task.get("shot_no") or task.get("id"),
    )
    return out


def _generate_video_with_refs(pid: str, batch: dict, task: dict, params: dict,
                              items: list, *, filename_prefix: str,
                              prompt_prefix: str = "",
                              required_roles: set[str] | None = None) -> dict:
    """Generate video with exactly the configured, priority-capped ref set."""
    aspect_ratio = params.get("aspect_ratio", "16:9")
    resolution = params.get("resolution", "720p")
    stage_cb = params.get("_stage_cb")
    max_refs = max(1, _max_refs())
    kept, prompt = _finalize_ref_items(task, items, max_refs=max_refs,
                                       required_roles=required_roles)
    refs = [it["b64"] for it in kept]
    if not refs:
        raise RuntimeError("图生视频缺少可用参考图：请先为该分镜生成或选择图片素材，系统不会退化为文生视频")
    if prompt_prefix:
        prompt = f"{prompt_prefix}\n{prompt}"
    refs = _normalize_refs_aspect(refs, aspect_ratio, resolution)
    ref_meta = [
        {
            "index": idx + 1,
            "token": f"@图{idx + 1}",
            "role": it.get("role") or "unknown",
            "name": it.get("name") or "",
            "filename": it.get("filename") or "",
            "sha256": hashlib.sha256(base64.b64decode(refs[idx])).hexdigest() if idx < len(refs) and refs[idx] else "",
        }
        for idx, it in enumerate(kept)
    ]
    logger.info(
        "[Video][Request] build batch=%s task=%s shot=%s refs=%s ref_meta=%s prompt_chars=%s duration=%s aspect=%s resolution=%s timeout=%s poll_interval=%s",
        batch.get("id"), task.get("id"), task.get("shot_no") or filename_prefix,
        len(refs), ref_meta, len(prompt or ""), _shot_duration(task, params),
        aspect_ratio, resolution, _video_timeout(params), _video_poll_interval(params),
    )
    try:
        batches.update_task(pid, batch["id"], task["id"], {
            "prompt": prompt,
            "refs": _serialize_ref_items(pid, kept),
            "ref_meta": ref_meta,
        })
    except Exception as e:  # noqa: BLE001
        logger.warning("[Refs] failed to persist task refs: %s", e)

    def _persist_prepared_refs(mapping: list[dict], urls: list[str]) -> None:
        try:
            transport = "public_url"
            if mapping and all((m.get("host") == "seedance-multipart") for m in mapping):
                transport = "seedance-multipart"
            elif mapping and all((m.get("host") == "data-url") for m in mapping):
                transport = "data_url"
            batches.update_task(pid, batch["id"], task["id"], {
                "reference_image_mapping": mapping,
                "reference_image_urls": urls,
                "reference_preflight": {
                    "status": "prepared",
                    "count": len(mapping) or len(urls),
                    "transport": transport,
                    "updated_at": time.time(),
                },
            })
        except Exception as e:  # noqa: BLE001
            logger.warning("[Refs] failed to persist prepared ref urls: %s", e)

    def _persist_submit_response(resp: dict) -> None:
        try:
            is_doubao_pool = isinstance(resp, dict) and resp.get("provider") == "doubao_pool"
            task_id = str(resp.get("task_id") or "") if is_doubao_pool else (video_gen._extract_task_id(resp) if isinstance(resp, dict) else "")
            transport = "doubao-pool" if is_doubao_pool else ("seedance-multipart" if _is_seedance_video_params(params) else "data_url")
            doubao_patch = {}
            if is_doubao_pool:
                doubao_patch = {
                    "doubao_task_id": task_id,
                    "doubao_account_id": str(resp.get("account_id") or ""),
                    "doubao_initial_account_id": str(resp.get("initial_account_id") or resp.get("account_id") or ""),
                    "doubao_shot_no": str(resp.get("shot_no") or task.get("shot_no") or filename_prefix or ""),
                    "doubao_account_switch_policy": str(resp.get("account_switch_policy") or "pre-submit-only"),
                }
            batches.update_task(pid, batch["id"], task["id"], {
                "reference_preflight": {
                    "status": "submitted",
                    "count": len(refs),
                    "transport": transport,
                    "video_task_id": task_id,
                    "updated_at": time.time(),
                },
                **({k: v for k, v in doubao_patch.items() if v} if is_doubao_pool else {}),
            })
        except Exception as e:  # noqa: BLE001
            logger.warning("[Refs] failed to persist submit response: %s", e)

    def _cancel_checker() -> bool:
        if is_paused(batch["id"]):
            raise BatchPaused("已停止")
        fresh = batches.get_batch(pid, batch["id"]) or {}
        return bool(fresh.get("pause_requested"))

    return video_gen.generate_video(
        prompt,
        model=params.get("model"),
        duration=_shot_duration(task, params),
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        ref_images_b64=refs,
        save_dir=_out_dir(pid, batch["id"]),
        filename_prefix=filename_prefix,
        timeout=_video_timeout(params),
        poll_interval=_video_poll_interval(params),
        generate_audio=bool(params.get("generate_audio", False)),
        watermark=bool(params.get("watermark", False)),
        on_stage=stage_cb,
        ref_meta=ref_meta,
        on_refs_prepared=_persist_prepared_refs,
        on_submit_response=_persist_submit_response,
        cancel_checker=_cancel_checker,
    )


def _parse_aspect(aspect_ratio: str) -> tuple[int, int]:
    try:
        w, h = (aspect_ratio or "16:9").split(":")
        return int(w), int(h)
    except Exception:  # noqa: BLE001
        return 16, 9


def _video_canvas_size(aspect_ratio: str, resolution: str) -> tuple[int, int]:
    aw, ah = _parse_aspect(aspect_ratio)
    res = str(resolution or "720p").strip().lower()
    short = 1080 if res == "1080p" else 480 if res == "480p" else 720
    if aw >= ah:
        width = int(round(short * aw / ah))
        height = short
    else:
        width = short
        height = int(round(short * ah / aw))
    return max(1, width), max(1, height)


def _normalize_refs_aspect(refs: list, aspect_ratio: str, resolution: str = "720p") -> list:
    """Fit references to the exact requested video canvas.

    Some upstream image-to-video relays choose output aspect from the first
    reference image. Sending references on the target canvas keeps them aligned
    with ``aspect_ratio`` instead of letting a square reference force square
    output.
    """
    if not refs:
        return refs
    width, height = _video_canvas_size(aspect_ratio, resolution)
    out = []
    for idx, r in enumerate(refs):
        try:
            out.append(ffmpeg_util.fit_b64_to_size(r, width, height))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[Refs] 参考图画布归一化跳过 #{idx + 1}: {e}")
            out.append(r)
    logger.info(f"[Refs] 视频参考图画布归一化 refs={len(out)} size={width}x{height} aspect={aspect_ratio}")
    return out


def _out_dir(pid: str, bid: str):
    d = OUTPUT_DIR / pid / bid
    d.mkdir(parents=True, exist_ok=True)
    return d


# Real generators
def _gen_image_task(pid, batch, task, params):
    refs, prompt = _finalize_refs(task, _asset_ref_items(pid, task), kind="image")
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
    out = _generate_video_with_refs(
        pid, batch, task, params, _asset_ref_items(pid, task),
        filename_prefix=task.get("shot_no") or task["id"],
    )
    return {
        "filename": out["filename"],
        "filepath": out["filepath"],
        "video_url": out.get("video_url", ""),
        "reference_image_urls": out.get("reference_image_urls", []),
        "reference_image_mapping": out.get("reference_image_mapping", []),
        "reference_transport": out.get("reference_transport", ""),
        "seedance_status": out.get("seedance_status", {}),
        "seedance_usage": out.get("seedance_usage", {}),
    }


def _gen_video_task_continuity(pid, batch, task, params):
    """Video generation with cross-shot continuity."""
    shot_no = task.get("shot_no") or task["id"]
    stage_cb = params.get("_stage_cb")
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
    # Enable LLM-assisted continuity decisions unless explicitly disabled.
    # decide_handoff falls back to deterministic rules if the LLM is unavailable.
    manual_map = params.get("manual_continuity") or {}
    manual_decision = manual_map.get(shot_no) if isinstance(manual_map, dict) else None
    if isinstance(manual_decision, dict):
        decision = continuity.normalize_decision({
            "scene_cut": not (
                manual_decision.get("use_tail_frame")
                or manual_decision.get("use_staging")
                or manual_decision.get("use_director_board")
            ),
            "use_tail_frame": bool(manual_decision.get("use_tail_frame")),
            "use_staging": bool(manual_decision.get("use_staging")),
            "use_director_board": bool(manual_decision.get("use_director_board")),
            "prefer_cut": not bool(manual_decision.get("use_tail_frame")),
            "long_take": bool(manual_decision.get("use_tail_frame")),
            "strategy": manual_decision.get("strategy") or "manual",
            "camera_hint": manual_decision.get("camera_hint", ""),
            "reason": manual_decision.get("reason") or "Manual continuity bridge selection",
            "source": "manual",
        })
    else:
        use_llm = bool(params.get("decision_llm", True))
        decision = continuity.decide_handoff(shot, prev_state, use_llm=use_llm,
                                             model=params.get("llm_model"))
    decision = continuity.ensure_bridge_context(
        continuity.enforce_director_policy(decision, shot), shot, prev_state)
    decision = _reconcile_tail_decision(decision, task)
    decision = continuity.ensure_bridge_context(decision, shot, prev_state)
    task["_bridge_data"] = decision.get("bridge_data") or {}
    task["_bridge_context"] = continuity.render_bridge_context(
        task["_bridge_data"], purpose="video").strip() or decision.get("bridge_context", "")
    logger.info(
        "[Continuity] %s decision[%s]: scene_cut=%s tail=%s staging=%s director=%s long_take=%s reason=%s",
        shot_no, decision.get("source", "rule"), decision.get("scene_cut"),
        decision.get("use_tail_frame"), decision.get("use_staging"),
        decision.get("use_director_board"), decision.get("long_take"),
        decision.get("reason", ""),
    )

    aspect_ratio = params.get("aspect_ratio", "16:9")

    if decision.get("use_tail_frame"):
        tail_b64 = continuity._read_b64(pid, prev_state.get("tail_frame"))
        if not tail_b64:
            raise RuntimeError("连续性决策要求使用上一镜尾帧，但上一镜尾帧缺失或读取失败；已停止以避免少图提交")

    story_state.set_decision(pid, shot_no, decision)
    batches.update_task(pid, batch["id"], task["id"], {
        "decision": decision,
        "continuity_strategy": decision.get("strategy"),
        "continuity_source": decision.get("source"),
        "src_text": task.get("src_text", ""),
    })

    if decision.get("use_staging") or decision.get("use_director_board"):
        snap0 = story_state.get_shot_state(pid, shot_no) or {}
        proj_assets = assets_core.list_assets(pid)
        if decision.get("use_staging") and not snap0.get("staging_image"):
            try:
                if stage_cb:
                    stage_cb("continuity_staging", "生成站位图中", None)
                continuity.generate_staging(
                    pid, shot, prev_state, style=style, assets=proj_assets,
                    bridge_context=continuity.render_bridge_context(
                        decision.get("bridge_data") or {}, purpose="staging"),
                )
                logger.info("[Continuity] %s generated staging image by decision", shot_no)
            except Exception as e:  # noqa: BLE001
                logger.warning("[Continuity] %s staging generation failed: %s", shot_no, e)
                raise RuntimeError(f"连续性决策要求使用站位图，但站位图生成失败：{e}") from e
        if decision.get("use_staging") and not (story_state.get_shot_state(pid, shot_no) or {}).get("staging_image"):
            raise RuntimeError("连续性决策要求使用站位图，但本镜站位图缺失或读取失败；已停止以避免少图提交")
        if decision.get("use_director_board") and not snap0.get("director_board"):
            try:
                if stage_cb:
                    stage_cb("continuity_director", "生成导演图中", None)
                continuity.generate_director_board(
                    pid, shot, prev_state, style=style, assets=proj_assets,
                    bridge_context=continuity.render_bridge_context(
                        decision.get("bridge_data") or {}, purpose="director"),
                )
                logger.info("[Continuity] %s generated director board by decision", shot_no)
            except Exception as e:  # noqa: BLE001
                logger.warning("[Continuity] %s director generation failed: %s", shot_no, e)
                raise RuntimeError(f"连续性决策要求使用导演图，但导演图生成失败：{e}") from e

    items: list = _asset_ref_items(pid, task)
    if decision.get("use_tail_frame"):
        tail_b64 = continuity._read_b64(pid, prev_state.get("tail_frame"))
        if tail_b64:
            items.append({"b64": tail_b64, "role": "first_frame", "name": "上一镜尾帧"})
        else:
            raise RuntimeError("连续性决策要求使用上一镜尾帧，但尾帧缺失或读取失败；已停止以避免少图提交")
    snap = story_state.get_shot_state(pid, shot_no) or {}
    director_b64 = continuity._read_b64(pid, snap.get("director_board"))
    if decision.get("use_director_board") and director_b64:
        items.append({"b64": director_b64, "role": "director", "name": "本镜导演图"})
        logger.info("[Continuity] %s director board participates as a video reference", shot_no)
    elif decision.get("use_director_board"):
        raise RuntimeError("复杂镜头已判定需要导演图，但本镜导演图缺失或读取失败；已停止以避免无导演图引导")
    staging_b64 = continuity._read_b64(pid, snap.get("staging_image"))
    if decision.get("use_staging") and staging_b64:
        items.append({"b64": staging_b64, "role": "staging", "name": "本镜站位图"})
    elif decision.get("use_staging"):
        raise RuntimeError("连续性决策要求使用站位图，但站位图读取失败；已停止以避免少图提交")

    story_state.set_decision(pid, shot_no, decision)
    batches.update_task(pid, batch["id"], task["id"], {
        "decision": decision,
        "continuity_strategy": decision.get("strategy"),
        "continuity_source": decision.get("source"),
    })

    hint = decision.get("camera_hint")
    bridge = continuity.render_bridge_context(
        decision.get("bridge_data") or {}, purpose="video").strip()
    if not bridge:
        bridge = _compact_bridge_context(decision.get("bridge_context") or "")
    prefix_lines = []
    existing_prompt = task.get("prompt") or ""
    if bridge and "连续性桥接变量" not in existing_prompt:
        prefix_lines.append(f"【连续性桥接变量】{bridge}")
    if hint and "【镜头衔接】" not in existing_prompt:
        prefix_lines.append(f"【镜头衔接】{hint}")
    required_roles = set()
    if decision.get("use_tail_frame"):
        required_roles.add("first_frame")
    if decision.get("use_staging"):
        required_roles.add("staging")
    if decision.get("use_director_board"):
        required_roles.add("director")
    out = _generate_video_with_refs(
        pid, batch, task, params, items,
        filename_prefix=shot_no,
        prompt_prefix="\n".join(prefix_lines),
        required_roles=required_roles,
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

    snapshot = {
        "src_text": task.get("src_text", ""),
        "decision": decision,
        "continuity_strategy": decision.get("strategy"),
        "continuity_source": decision.get("source"),
        "tail_frame": tail_info["tail_frame"] if tail_info else None,
        "review": review,
    }
    result = {
        "filename": out["filename"],
        "filepath": out["filepath"],
        "video_url": out.get("video_url", ""),
        "reference_image_urls": out.get("reference_image_urls", []),
        "reference_image_mapping": out.get("reference_image_mapping", []),
        "reference_transport": out.get("reference_transport", ""),
        "seedance_status": out.get("seedance_status", {}),
        "seedance_usage": out.get("seedance_usage", {}),
        "src_text": snapshot["src_text"],
        "decision": snapshot["decision"],
        "continuity_strategy": snapshot["continuity_strategy"],
        "continuity_source": snapshot["continuity_source"],
        "snapshot": snapshot,
    }
    if tail_info:
        result["tail_frame"] = tail_info["tail_frame"]
    if review is not None:
        result["review"] = review
    return result


_GENERATORS: dict[str, Callable] = {"image": _gen_image_task, "video": _gen_video_task}


def _mark_preflight_blocked(pid: str, bid: str, pending: list[dict], err_info: dict) -> dict:
    if not pending:
        batches.set_status(pid, bid, "error")
        return batches.get_batch(pid, bid) or {}
    for idx, task in enumerate(pending):
        if idx == 0:
            batches.update_task(pid, bid, task["id"], {
                "status": "error",
                "error": err_info,
                "attempts": task.get("attempts", 0),
                "stage": "preflight_failed",
                "stage_label": "豆包号池不可用",
                "progress": 100,
            })
        else:
            batches.update_task(pid, bid, task["id"], {
                "status": "skipped",
                "error": None,
                "skip_reason": err_info.get("message") or "批次启动前置条件未通过",
                "attempts": task.get("attempts", 0),
                "stage": "skipped",
                "stage_label": "已跳过：豆包号池不可用",
                "progress": 100,
            })
    batches.set_status(pid, bid, "error")
    return batches.get_batch(pid, bid) or {}


def _mark_pending_skipped_after_abort(pid: str, bid: str, final: dict) -> dict:
    for task in final.get("tasks", []):
        if task.get("status") == "pending":
            batches.update_task(pid, bid, task["id"], {
                "status": "skipped",
                "error": None,
                "skip_reason": "前序致命错误中止本批，未消耗调用。",
                "stage": "skipped",
                "stage_label": "已跳过：前序错误",
                "progress": 100,
            })
    return batches.get_batch(pid, bid) or final


# Runner
def _run_one(pid, batch, task, gen, params, backoff):
    """Run one task with retry/error handling."""
    bid = batch["id"]
    label = task.get("shot_no") or task["id"]
    if is_paused(bid):
        return "paused"
    def stage_cb(stage: str, label: str, progress=None):
        fresh = batches.get_batch(pid, bid) or {}
        fresh_task = next((t for t in fresh.get("tasks", []) if t.get("id") == task["id"]), {})
        if fresh.get("pause_requested") or fresh_task.get("status") == "paused":
            raise BatchPaused("已停止")
        patch = {"stage": stage, "stage_label": label}
        if progress is not None:
            try:
                patch["progress"] = max(0, min(100, int(progress)))
            except Exception:  # noqa: BLE001
                pass
        batches.update_task(pid, bid, task["id"], patch)

    batches.update_task(pid, bid, task["id"], {
        "status": "running",
        "stage": "starting",
        "stage_label": "准备中",
        "progress": 0,
    })
    attempts = task.get("attempts", 0)
    # Video generation is paid and content-sensitive: do not auto-resubmit a
    # failed video prompt, even when the provider/error classifier marks the
    # failure as retryable. The UI will surface the error and let the user edit
    # the prompt/settings before manually generating again.
    max_attempts = 1 if batch.get("kind") == "video" else max(1, task.get("max_attempts", 3))
    err_info = {"category": "unknown", "message": "未执行", "retryable": False}
    while attempts < max_attempts:
        attempts += 1
        try:
            slot = _global_generation_slots()
            stage_cb("waiting_slot", "等待空闲生成位", None)
            while not slot.acquire(timeout=0.5):
                if is_paused(bid):
                    batches.update_task(pid, bid, task["id"], {
                        "status": "paused",
                        "stage": "paused",
                        "stage_label": "已暂停",
                    })
                    return "paused"
            try:
                if is_paused(bid):
                    batches.update_task(pid, bid, task["id"], {
                        "status": "paused",
                        "stage": "paused",
                        "stage_label": "已暂停",
                    })
                    return "paused"
                task_params = {**params, "_stage_cb": stage_cb} if batch.get("kind") == "video" else params
                result = gen(pid, batch, task, task_params)
            finally:
                slot.release()
            if isinstance(result, dict) and result.get("manual_required"):
                batches.update_task(pid, bid, task["id"], {
                    "status": "skipped",
                    "result": result,
                    "attempts": attempts,
                    "error": None,
                    "skip_reason": result.get("message") or "豆包半自动模式已填入素材，等待手动生成",
                    "stage": "manual",
                    "stage_label": "豆包半自动：待手动生成",
                    "progress": 100,
                })
                return "done"
            batches.update_task(pid, bid, task["id"],
                                {"status": "done", "result": result, "attempts": attempts, "error": None,
                                 "stage": "done", "stage_label": "已完成", "progress": 100})
            return "done"
        except Exception as e:  # noqa: BLE001
            if isinstance(e, BatchPaused):
                batches.update_task(pid, bid, task["id"], {
                    "status": "paused",
                    "stage": "paused",
                    "stage_label": "已暂停",
                })
                return "paused"
            if isinstance(e, video_gen.DoubaoSemiAutoReady):
                doubao_task = e.task or {}
                result = {
                    "provider": "doubao_pool",
                    "manual_required": True,
                    "message": str(e),
                    "doubao_task_id": doubao_task.get("id") or "",
                    "doubao_account_id": doubao_task.get("accountId") or "",
                    "doubao_stage": doubao_task.get("stage") or "semi-auto-ready",
                    "doubao_automation_mode": doubao_task.get("doubaoAutomationMode") or doubao_task.get("automationMode") or "semi",
                }
                batches.update_task(pid, bid, task["id"], {
                    "status": "skipped",
                    "result": result,
                    "attempts": attempts,
                    "error": None,
                    "skip_reason": result["message"],
                    "stage": "manual",
                    "stage_label": "豆包半自动：待手动生成",
                    "progress": 100,
                    "doubao_task_id": result["doubao_task_id"],
                    "doubao_account_id": result["doubao_account_id"],
                })
                return "done"
            err_info = error_policy.classify(e)
            logger.warning(
                "[Batch %s] task %s failed on attempt %s [%s%s]: %s",
                bid, label, attempts, err_info["category"],
                ("/" + err_info["code"]) if err_info.get("code") else "",
                err_info["message"],
            )
            if err_info["retryable"] and attempts < max_attempts:
                time.sleep(backoff * attempts)  # progressive backoff
                continue
            break  # non-retryable: stop immediately to avoid wasting quota
    # record the structured error and the number of attempts actually spent
    err_info = {**err_info, "attempts_used": attempts}
    batches.update_task(pid, bid, task["id"],
                        {"status": "error", "error": err_info, "attempts": attempts,
                         "stage": "error", "stage_label": "失败"})
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
    batches.clear_pause_request(pid, bid)
    batch = batches.get_batch(pid, bid) or batch
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
    pending = [t for t in all_tasks if t.get("status") not in {"done", "skipped"}]  # resume
    done_count = total - len(pending)
    preflight_error = _doubao_pool_preflight_error(batch, pending)
    if preflight_error:
        final = _mark_preflight_blocked(pid, bid, pending, preflight_error)
        if on_progress:
            on_progress(
                sum(1 for t in final.get("tasks", []) if t.get("status") in ("done", "error", "skipped")),
                total,
            )
        logger.warning("[Batch %s] doubao pool preflight blocked: %s", bid, preflight_error.get("message"))
        statuses = [t.get("status") for t in final.get("tasks", [])]
        return {
            "status": "error",
            "total": total,
            "done": sum(1 for s in statuses if s == "done"),
            "error": sum(1 for s in statuses if s == "error"),
            "skipped": sum(1 for s in statuses if s == "skipped"),
        }
    batches.set_status(pid, bid, "running")

    def report():
        if on_progress:
            on_progress(done_count, total)

    report()
    aborted = False
    workers = min(concurrency, _global_limit(), max(1, len(pending)))
    cont_lock = _continuity_lock(pid) if continuity_on else None
    cont_lock_acquired = False
    if cont_lock:
        logger.info("[Batch %s] waiting for project continuity lock", bid)
        while not cont_lock.acquire(timeout=0.5):
            if is_paused(bid):
                batches.set_status(pid, bid, "paused")
                return batches.get_batch(pid, bid)
        cont_lock_acquired = True
        logger.info("[Batch %s] acquired project continuity lock", bid)
    try:
        if continuity_on:
            for t in pending:
                outcome = _run_one(pid, batch, t, gen, params, backoff)
                if outcome in ("done", "error", "fatal"):
                    done_count += 1
                    report()
                if outcome == "fatal":
                    aborted = True
                    break
                if outcome == "paused":
                    break
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(_run_one, pid, batch, t, gen, params, backoff): t for t in pending}
                for fut in as_completed(futures):
                    t = futures[fut]
                    try:
                        outcome = fut.result()
                    except Exception as e:  # noqa: BLE001
                        err_info = error_policy.classify(e)
                        batches.update_task(pid, bid, t["id"], {
                            "status": "error",
                            "error": {**err_info, "attempts_used": t.get("attempts", 0)},
                            "stage": "error",
                            "stage_label": "失败",
                        })
                        logger.exception("[Batch %s] worker crashed for task %s", bid, t.get("shot_no") or t.get("id"))
                        outcome = "error"
                    if outcome in ("done", "error", "fatal"):
                        done_count += 1
                        report()
                    if outcome == "fatal":
                        aborted = True
    finally:
        if cont_lock_acquired:
            cont_lock.release()
            logger.info("[Batch %s] released project continuity lock", bid)

    final = batches.get_batch(pid, bid)
    # when aborted by a fatal error, mark the still-pending shots as skipped so
    # the UI explains why they didn't run (rather than leaving them "pending")
    if aborted:
        final = _mark_pending_skipped_after_abort(pid, bid, final)
    statuses = [t["status"] for t in final["tasks"]]
    if aborted:
        status = "error"
    elif is_paused(bid) and any(s in ("pending", "paused") for s in statuses):
        status = "paused"
    elif any(s == "error" for s in statuses):
        status = "error"
    elif all(s in {"done", "skipped"} for s in statuses):
        status = "done"
    else:
        status = "paused"
    batches.set_status(pid, bid, status)
    _clear_pause(bid)
    return {
        "status": status,
        "total": total,
        "done": sum(1 for s in statuses if s in {"done", "skipped"}),
        "error": sum(1 for s in statuses if s == "error"),
        "skipped": sum(1 for s in statuses if s == "skipped"),
    }
