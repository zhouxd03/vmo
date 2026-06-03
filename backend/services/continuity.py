"""Continuity engine (Phase 5).

Ties together the StoryState memory, ffmpeg tail-frame extraction, staging /
director-board generation, and the AI multimodal review gate. Each shot, before
generation, gets a *handoff decision* (rule-based, optionally upgraded by an
LLM) that picks the continuity mechanism:

  - 尾帧 (tail frame)  : feed the previous shot's last frame as首帧参考
  - 站位图 (staging)   : a top-down blocking image to lock character positions
  - 导演图 (director)  : a 16:9 storyboard blueprint for a new block
  - 直接生成 (direct)  : scene cut — no carry-over

In auto mode the engine inserts an AI continuity review before committing, so a
wrong handoff can be caught instead of silently producing a broken shot.
"""

import base64
import logging
from pathlib import Path
from typing import Any, Optional

from ..core import assets as assets_core
from ..core import prompt_templates as tpl
from ..core import settings as settings_store
from ..core import story_state
from ..core.paths import PROJECTS_DIR
from . import ffmpeg_util, image_gen, llm, reference_set

logger = logging.getLogger("batch_studio")


# ── shot → state shaping ────────────────────────────────────────────────────
def shot_to_state(shot: dict) -> dict:
    """Project a decomposed shot into a StoryState snapshot patch."""
    chars = [{"name": c, "position": "", "pose": ""} for c in (shot.get("characters") or [])]
    props = [{"name": p, "state": ""} for p in (shot.get("props") or [])]
    return {
        "scene": shot.get("scene", ""),
        "characters": chars,
        "props": props,
        "lighting": shot.get("emotion", ""),
        "director_note": shot.get("handoff", "") or shot.get("action", ""),
    }


def _names(items) -> set:
    out = set()
    for it in items or []:
        if isinstance(it, dict):
            n = it.get("name")
        else:
            n = it
        if n:
            out.add(str(n).lstrip("@#$"))
    return out


# 仅当分镜明确标注「必须连续的长镜头」时才承接上一镜尾帧（首尾帧相接）。
# 该中转的图生视频不支持精确首帧，尾帧承接只能做软参考、易跳帧/色差，
# 故默认改用切镜头/景别切换衔接，长镜头才例外。
LONG_TAKE_KW = (
    "长镜头", "一镜到底", "不切", "不间断", "不剪", "同一镜头", "同一长镜",
    "连续运镜", "连贯运动", "紧接上", "承接上一镜", "顺接", "无缝衔接", "无缝承接",
    "long take", "oner", "continuous shot", "single take",
)


def _is_long_take(shot: dict) -> bool:
    text = " ".join(str(shot.get(k, "") or "") for k in ("handoff", "camera", "action"))
    return any(kw in text for kw in LONG_TAKE_KW)


# ── handoff decision ────────────────────────────────────────────────────────
def decide_handoff_rule(shot: dict, prev_state: dict) -> dict:
    """Deterministic decision (no credentials needed) — also the LLM fallback.

    衔接策略：默认用「切镜头 / 景别切换」做画面衔接（重新生成、不承接尾帧），
    仅当分镜明确是「必须连续的长镜头」时才承接上一镜尾帧。这样规避了该中转
    图生视频首帧承接做不到、易跳帧/色差的问题。
    """
    prev_scene = (prev_state or {}).get("scene", "") or ""
    cur_scene = shot.get("scene", "") or ""
    has_prev = bool((prev_state or {}).get("shot_no"))
    prev_chars = _names((prev_state or {}).get("characters"))
    cur_chars = _names(shot.get("characters"))
    overlap = bool(prev_chars & cur_chars)

    def _norm(s):
        return s.lstrip("@#$").strip()

    scene_cut = (not has_prev) or (_norm(prev_scene) != _norm(cur_scene))
    has_tail = bool((prev_state or {}).get("tail_frame"))
    long_take = _is_long_take(shot)
    # 默认不承接尾帧；只有「同场景 + 有尾帧 + 明确长镜头」才承接
    use_tail = (not scene_cut) and has_tail and long_take
    use_staging = (not scene_cut) and overlap
    prefer_cut = has_prev and not use_tail  # 用切镜头/景别切换衔接

    if not has_prev:
        reason = "首镜，无上文，直接生成"
        camera_hint = "用明确的景别建立场景与人物。"
    elif scene_cut:
        reason = f"场景由「{_norm(prev_scene) or '—'}」切到「{_norm(cur_scene) or '—'}」，切场景直生"
        camera_hint = "切换到新场景：用一个全新机位/景别建立新环境，不要延续上一镜构图。"
    elif use_tail:
        reason = "必须连续的长镜头：承接上一镜尾帧，运动与构图自然延续、不跳切"
        camera_hint = "长镜头连续承接上一镜，运动、构图与光线自然延续，避免跳切。"
    else:
        who = "、".join(sorted(prev_chars & cur_chars))
        reason = ("同场景切镜头衔接（景别切换）"
                  + (f"，延续在场人物（{who}）站位" if use_staging else ""))
        camera_hint = ("切镜头衔接：换一个与上一镜不同的景别/机位"
                       "（上一镜近景则本镜转中/全景，上一镜全景则本镜转近/特写），"
                       "通过景别变化自然过场，避免与上一镜构图雷同导致跳帧感。")
    return {
        "scene_cut": scene_cut,
        "use_tail_frame": use_tail,
        "use_staging": use_staging,
        "use_director_board": False,
        "long_take": long_take,
        "prefer_cut": prefer_cut,
        "camera_hint": camera_hint,
        "reason": reason,
        "source": "rule",
    }


def decide_handoff(shot: dict, prev_state: dict, *, use_llm: bool = False,
                   model: Optional[str] = None) -> dict:
    """Pick the continuity mechanism for *shot*.

    Defaults to the deterministic rule. When *use_llm* is set and an LLM
    credential is available, asks the model for a richer judgement but always
    falls back to the rule on error.
    """
    rule = decide_handoff_rule(shot, prev_state)
    if not use_llm:
        return rule
    try:
        body = tpl.get_template_body("handoff_decision")
        prompt = tpl.render(body, {
            "prev_state": _state_text(prev_state),
            "scene": shot.get("scene", ""),
            "characters": "、".join(sorted(_names(shot.get("characters")))) or "（无）",
            "props": "、".join(sorted(_names(shot.get("props")))) or "（无）",
            "action": shot.get("action", ""),
            "camera": shot.get("camera", ""),
        })
        out = llm.chat_json([{"role": "user", "content": prompt}], model=model, temperature=0.2)
        if isinstance(out, dict):
            out.setdefault("reason", rule["reason"])
            out["source"] = "llm"
            # tail frame still requires one to actually exist
            if not (prev_state or {}).get("tail_frame"):
                out["use_tail_frame"] = False
            # 强制衔接策略：仅「明确长镜头」才允许承接尾帧，否则一律切镜头/景别切换，
            # 避免 LLM 误判频繁首尾帧相接（该中转做不到、易跳帧/色差）。
            if not rule.get("long_take"):
                if out.get("use_tail_frame"):
                    out["reason"] = "非长镜头 → 改切镜头/景别切换衔接｜" + str(out.get("reason", ""))
                out["use_tail_frame"] = False
            out["long_take"] = rule.get("long_take", False)
            out["prefer_cut"] = not out.get("use_tail_frame")
            # 继承规则给出的运镜/景别提示，供生成提示词使用
            out.setdefault("camera_hint", rule.get("camera_hint", ""))
            return out
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[Continuity] LLM 决策失败，回退规则: {e}")
    return rule


def _state_text(state: dict) -> str:
    state = state or {}
    if not state.get("shot_no"):
        return "（首镜，无上文）"
    chars = "、".join(
        f"{c.get('name','')}{('@'+c['position']) if c.get('position') else ''}"
        for c in state.get("characters", [])
    )
    props = "、".join(f"{p.get('name','')}" for p in state.get("props", []))
    return (
        f"上一镜 {state.get('shot_no')}｜场景: {state.get('scene','')}｜"
        f"人物: {chars or '—'}｜道具: {props or '—'}｜"
        f"光线情绪: {state.get('lighting','')}｜导演接口: {state.get('director_note','')}"
        f"｜尾帧: {'有' if state.get('tail_frame') else '无'}"
    )


# ── tail frame ──────────────────────────────────────────────────────────────
def extract_tail_frame(pid: str, shot_no: str, video_path: str | Path) -> dict:
    out_name = ffmpeg_util.tail_frame_filename(shot_no)
    out_path = story_state.continuity_dir(pid) / out_name
    ffmpeg_util.extract_tail_frame(video_path, out_path)
    story_state.set_tail_frame(pid, shot_no, out_name)
    return {"tail_frame": out_name, "path": str(out_path)}


# ── staging / director images ───────────────────────────────────────────────
def _read_b64(pid: str, filename: Optional[str]) -> Optional[str]:
    if not filename:
        return None
    path = story_state.continuity_dir(pid) / filename
    if path.exists():
        return base64.b64encode(path.read_bytes()).decode("utf-8")
    return None


def _max_refs() -> int:
    try:
        return int(settings_store.load_settings().get("max_reference_images", 8))
    except (TypeError, ValueError):
        return 8


def _shot_asset_refs(pid: str, shot: dict, assets: Optional[list]) -> list:
    """Capped @/#/$ reference images for a shot, for staging/director generation.

    Mirrors the video-gen path so 导演图对图片的引用 also respects the priority
    cap (导演图/首帧图 ＞ 角色图 ＞ 背景图 ＞ 配角图 ＞ 道具图).
    """
    if assets is None:
        return []
    text = "，".join(p for p in [
        shot.get("scene", ""), shot.get("action", ""),
        " ".join(_names(shot.get("characters"))), " ".join(_names(shot.get("props"))),
    ] if p)
    res = assets_core.resolve_mentions(text, assets)
    items = []
    for m in res["materials"]:
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
        })
    return [it["b64"] for it in reference_set.cap(items, _max_refs())]


def generate_staging(pid: str, shot: dict, prev_state: dict, *,
                     style: str = "", model: Optional[str] = None,
                     size: str = "1024x1024", assets: Optional[list] = None) -> dict:
    body = tpl.get_template_body("staging_diagram")
    prompt = tpl.render(body, {
        "style": style,
        "scene": shot.get("scene", ""),
        "characters": "、".join(sorted(_names(shot.get("characters")))) or "（无）",
        "prev_blocking": _state_text(prev_state),
        "props": "、".join(sorted(_names(shot.get("props")))) or "（无）",
    })
    out = image_gen.generate_image(
        prompt, model=model, size=size,
        ref_images_b64=_shot_asset_refs(pid, shot, assets),
        save_dir=story_state.continuity_dir(pid),
        filename_prefix=f"{(shot.get('shot_no') or 'shot')}_staging",
    )
    story_state.set_staging(pid, shot.get("shot_no", ""), out["filename"])
    return out


def generate_director_board(pid: str, shot: dict, prev_state: dict, *,
                            style: str = "", model: Optional[str] = None,
                            size: str = "1536x1024", assets: Optional[list] = None) -> dict:
    body = tpl.get_template_body("director_board")
    prompt = tpl.render(body, {
        "style": style,
        "scene": shot.get("scene", ""),
        "characters": "、".join(sorted(_names(shot.get("characters")))) or "（无）",
        "props": "、".join(sorted(_names(shot.get("props")))) or "（无）",
        "prev_handoff": (prev_state or {}).get("director_note", "") or "（无上文接口）",
        "action": shot.get("action", ""),
    })
    out = image_gen.generate_image(
        prompt, model=model, size=size,
        ref_images_b64=_shot_asset_refs(pid, shot, assets),
        save_dir=story_state.continuity_dir(pid),
        filename_prefix=f"{(shot.get('shot_no') or 'shot')}_director",
    )
    story_state.set_director_board(pid, shot.get("shot_no", ""), out["filename"])
    return out


# ── AI multimodal review gate ───────────────────────────────────────────────
def ai_review(pid: str, shot: dict, prev_state: dict, *,
              extra_images: Optional[list[str]] = None,
              model: Optional[str] = None) -> dict:
    """Send prev tail frame + proposed staging/refs + context to a vision LLM.

    Returns {pass, score, issues, suggestion, source}. Requires an LLM credential
    that supports image input; raises llm.LLMError otherwise.
    """
    st = story_state.get_state(pid)
    shot_no = shot.get("shot_no", "")
    snap = st["shots"].get(shot_no, {})

    images: list[str] = []
    tail_b64 = _read_b64(pid, (prev_state or {}).get("tail_frame"))
    if tail_b64:
        images.append(tail_b64)
    for key in ("staging_image", "director_board"):
        b = _read_b64(pid, snap.get(key))
        if b:
            images.append(b)
    for b in (extra_images or []):
        if b:
            images.append(b)

    context = (
        f"上一镜状态：{_state_text(prev_state)}\n"
        f"本镜 {shot_no}｜场景:{shot.get('scene','')}｜人物:"
        f"{'、'.join(sorted(_names(shot.get('characters')))) or '—'}｜"
        f"道具:{'、'.join(sorted(_names(shot.get('props')))) or '—'}｜"
        f"动作:{shot.get('action','')}｜机位:{shot.get('camera','')}"
    )
    prompt = tpl.render(tpl.get_template_body("continuity_review"), {"context": context})

    # the default LLM model may be text-only; fall back to the configured
    # vision model so the multimodal review works without manual model entry.
    review_model = model or settings_store.load_settings().get("vision_model")

    content: list[dict] = [{"type": "text", "text": prompt}]
    for b in images:
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b}"}})
    out = llm.chat_json([{"role": "user", "content": content}], model=review_model, temperature=0.2)
    if not isinstance(out, dict):
        raise llm.LLMError("复核未返回 JSON 对象")
    out.setdefault("pass", False)
    out.setdefault("score", 0)
    out.setdefault("issues", [])
    out["source"] = "llm"
    out["image_count"] = len(images)
    story_state.set_review(pid, shot_no, out)
    return out
