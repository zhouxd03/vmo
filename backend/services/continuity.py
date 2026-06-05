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
import re
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


_DIRECTOR_FIGHT_KW = (
    "打斗", "战斗", "搏斗", "追逐", "追击", "枪战", "刀战", "爆炸", "冲突", "混战", "群战",
    "fight", "battle", "combat", "chase", "explosion",
)
_DIRECTOR_CAMERA_KW = (
    "复杂运镜", "长镜头", "环绕", "绕拍", "跟拍", "推拉摇移", "快速切换", "多机位", "俯冲", "旋转",
    "穿梭", "大幅调度", "复杂调度", "one take", "long take", "tracking", "orbit", "dolly", "crane",
)
_DIRECTOR_PLOT_KW = (
    "复杂剧情", "多线", "反转", "关键转折", "信息量", "仪式", "审判", "谈判", "多人对峙", "群像",
    "多人物", "调度复杂", "空间关系复杂", "关键动作", "关键画面",
)


def needs_director_board(shot: dict) -> bool:
    """Only complex shots should spend/use a director board.

    Routine continuity uses staging + asset references. Director boards are
    reserved for fights, complex camera movement, complex plot beats, or dense
    multi-character blocking.
    """
    text = " ".join(str(shot.get(k, "") or "") for k in ("scene", "action", "camera", "handoff", "dialogue"))
    lowered = text.lower()
    if any(kw.lower() in lowered for kw in _DIRECTOR_FIGHT_KW):
        return True
    if any(kw.lower() in lowered for kw in _DIRECTOR_CAMERA_KW):
        return True
    if any(kw.lower() in lowered for kw in _DIRECTOR_PLOT_KW):
        return True
    chars = _names(shot.get("characters"))
    props = _names(shot.get("props"))
    return len(chars) >= 4 or (len(chars) >= 3 and len(props) >= 2)


_STAGING_SPATIAL_KW = (
    "对峙", "面对面", "反打", "过肩", "视线", "眼神", "左右", "左侧", "右侧", "前后",
    "站位", "位置", "距离", "靠近", "后退", "逼近", "绕到", "穿过", "遮挡", "挡住",
    "递给", "交给", "抢过", "拿起", "放下", "同桌", "围坐", "队形",
    "over shoulder", "shot reverse shot", "eyeline", "blocking", "position",
)
_CUT_FRIENDLY_KW = (
    "特写", "近景", "反应", "表情", "眼神", "沉默", "停顿", "回忆", "闪回",
    "心理", "情绪", "独白", "看向", "转头", "普通切", "切镜",
    "close-up", "reaction", "insert",
)


def _text_has(text: str, kws: tuple[str, ...]) -> bool:
    lowered = (text or "").lower()
    return any(kw.lower() in lowered for kw in kws)


def _needs_staging(shot: dict, prev_state: dict, *, scene_cut: bool, overlap: bool) -> bool:
    if scene_cut or not overlap:
        return False
    text = " ".join(str(shot.get(k, "") or "") for k in ("scene", "action", "camera", "handoff", "dialogue"))
    chars = _names(shot.get("characters"))
    props = _names(shot.get("props"))
    if len(chars) >= 3:
        return True
    if len(chars) >= 2 and (_text_has(text, _STAGING_SPATIAL_KW) or props):
        return True
    return _text_has(text, _STAGING_SPATIAL_KW) and not _text_has(text, _CUT_FRIENDLY_KW)


def enforce_director_policy(decision: dict, shot: dict) -> dict:
    if not decision.get("use_director_board") or needs_director_board(shot):
        return decision
    scene_cut = bool(decision.get("scene_cut"))
    use_staging = False if scene_cut else bool(decision.get("use_staging"))
    out = {
        **decision,
        "use_director_board": False,
        "use_staging": use_staging,
        "strategy": "staging" if use_staging else "scene_cut",
        "reason": (
            "导演图仅用于打斗/复杂运镜/复杂剧情等复杂画面；"
            + ("本镜保留站位图辅助空间关系。" if use_staging else "本镜改为普通切镜头/景别切换。")
            + f"原判定: {decision.get('reason', '')}"
        ),
    }
    out["scene_cut"] = not (out.get("use_tail_frame") or out.get("use_staging") or out.get("use_director_board"))
    return out


def _bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "y", "是", "开启", "需要")
    return False


def normalize_decision(decision: dict, rule: dict | None = None) -> dict:
    """Coerce LLM/manual continuity decision into the expected shape."""
    rule = rule or {}
    out = dict(decision or {})
    for key in ("scene_cut", "long_take", "use_tail_frame", "use_staging",
                "use_director_board", "prefer_cut"):
        if key in out:
            out[key] = _bool(out.get(key))
        elif key in rule:
            out[key] = _bool(rule.get(key))
    out.setdefault("scene_cut", not (
        out.get("use_tail_frame") or out.get("use_staging") or out.get("use_director_board")))
    out.setdefault("long_take", rule.get("long_take", False))
    out.setdefault("prefer_cut", not out.get("use_tail_frame"))
    out["camera_hint"] = str(out.get("camera_hint") or rule.get("camera_hint") or "").strip()
    out["reason"] = str(out.get("reason") or rule.get("reason") or "").strip()
    if out.get("use_tail_frame"):
        out["strategy"] = "tail_frame"
    elif out.get("use_director_board"):
        out["strategy"] = "director_board"
    elif out.get("use_staging"):
        out["strategy"] = "staging"
    elif out.get("scene_cut"):
        out["strategy"] = "scene_cut"
    else:
        out.setdefault("strategy", "cinematic_cut")
    return out


# ── handoff decision ────────────────────────────────────────────────────────
def build_bridge_data(decision: dict, shot: dict, prev_state: dict) -> dict:
    """Structured continuity handoff state for downstream prompt rendering."""
    decision = decision or {}
    shot = shot or {}
    prev_state = prev_state or {}

    prev = _state_text(prev_state)
    scene = shot.get("scene", "") or "(unset scene)"
    chars = ", ".join(sorted(_names(shot.get("characters")))) or "(no explicit characters)"
    props = ", ".join(sorted(_names(shot.get("props")))) or "(no explicit props)"
    action = (shot.get("action") or "").strip() or "(no explicit action)"
    camera = (shot.get("camera") or "").strip() or "(unset camera)"
    handoff = (shot.get("handoff") or "").strip()
    hint = (decision.get("camera_hint") or "").strip()

    if decision.get("use_tail_frame"):
        strategy_key = "tail_frame"
        strategy = "尾帧承接：仅用于真实长镜头/连续动作"
        carry = ["上一镜末态的人物姿势", "动作惯性", "光线方向", "构图重心"]
        avoid = ["不要在非长镜头里强行首尾帧相接", "不要丢弃已选择的尾帧参考"]
    elif decision.get("use_director_board"):
        strategy_key = "director_board"
        strategy = "导演图承接：用于复杂动作、复杂运镜或多人调度"
        carry = ["动作路径", "人物层次", "视线轴线", "叙事重心", "复杂运镜起止点"]
        avoid = ["不要把导演图当成唯一外观参考", "不要照抄信息图文字或标签"]
    elif decision.get("use_staging"):
        strategy_key = "staging"
        strategy = "站位图承接：用于同场景空间连续"
        carry = ["相对站位", "左右关系", "人物距离", "视线方向", "道具相对位置"]
        avoid = ["不要复制站位图的俯视草图风格", "不要生成文字标签", "不要改变左右关系"]
    elif decision.get("scene_cut"):
        strategy_key = "scene_cut"
        strategy = "切场/切景别承接"
        carry = ["必要人物情绪", "道具状态", "叙事信息"]
        avoid = ["不要强行沿用上一镜构图", "不要把切场误做尾帧连续"]
    else:
        strategy_key = "cinematic_cut"
        strategy = "普通影视切镜承接"
        carry = ["人物状态", "道具归属", "空间逻辑", "情绪节拍"]
        avoid = ["不要机械复述上一镜构图", "不要增加无关参考图约束"]

    return {
        "strategy_key": strategy_key,
        "strategy": strategy,
        "previous_interface": prev,
        "current_shot": {
            "scene": scene,
            "characters": chars,
            "props": props,
            "action": action,
            "camera": camera,
        },
        "carry": carry,
        "avoid": avoid,
        "camera_hint": hint,
        "next_variable": handoff,
        "selected_refs": {
            "tail_frame": bool(decision.get("use_tail_frame")),
            "staging": bool(decision.get("use_staging")),
            "director_board": bool(decision.get("use_director_board")),
        },
    }


def render_bridge_context(bridge_data: dict, *, purpose: str = "full") -> str:
    """Render structured bridge data for a specific downstream consumer."""
    data = bridge_data or {}
    shot = data.get("current_shot") or {}
    carry = [str(x).strip() for x in (data.get("carry") or []) if str(x).strip()]
    avoid = [str(x).strip() for x in (data.get("avoid") or []) if str(x).strip()]
    strategy = data.get("strategy") or ""
    camera_hint = data.get("camera_hint") or ""
    next_var = data.get("next_variable") or ""

    if purpose == "video":
        lines = [f"桥接策略：{strategy}"] if strategy else []
        if carry:
            lines.append(f"承接重点：{'、'.join(carry[:4])}")
        if camera_hint:
            lines.append(f"镜头提示：{camera_hint}")
        if next_var:
            lines.append(f"本镜收尾变量：{next_var}")
        return "；".join(lines)

    if purpose == "staging":
        lines = [
            f"本镜空间目标：场景={shot.get('scene', '')}；人物={shot.get('characters', '')}；道具={shot.get('props', '')}",
            f"站位承接：{'、'.join(carry[:5]) or strategy}",
        ]
        if avoid:
            lines.append(f"禁止：{'、'.join(avoid[:3])}")
        return "\n".join(x for x in lines if x)

    if purpose == "director":
        lines = [
            f"本镜调度目标：动作={shot.get('action', '')}；机位={shot.get('camera', '')}",
            f"导演图承接：{strategy}；重点={'、'.join(carry[:5])}",
        ]
        if camera_hint:
            lines.append(f"镜头提示：{camera_hint}")
        if avoid:
            lines.append(f"禁止：{'、'.join(avoid[:2])}")
        return "\n".join(x for x in lines if x)

    if purpose == "infer":
        lines = [f"桥接策略：{strategy}"]
        if carry:
            lines.append(f"只提炼这些承接点：{'、'.join(carry[:5])}")
        if avoid:
            lines.append(f"不要写入/不要误用：{'、'.join(avoid[:3])}")
        if next_var:
            lines.append(f"下一镜变量：{next_var}")
        return "\n".join(x for x in lines if x)

    lines = [
        f"上一镜接口：{data.get('previous_interface', '')}",
        (
            f"本镜目标：场景={shot.get('scene', '')}；人物={shot.get('characters', '')}；"
            f"道具={shot.get('props', '')}；动作={shot.get('action', '')}；机位={shot.get('camera', '')}"
        ),
        f"桥接策略：{strategy}",
        f"承接重点：{'、'.join(carry)}",
    ]
    if avoid:
        lines.append(f"禁止误用：{'、'.join(avoid)}")
    if camera_hint:
        lines.append(f"镜头提示：{camera_hint}")
    if next_var:
        lines.append(f"本镜交给下一镜的收尾变量：{next_var}")
    return "\n".join(x for x in lines if x)


def build_bridge_context(decision: dict, shot: dict, prev_state: dict, *,
                         purpose: str = "full") -> str:
    return render_bridge_context(
        build_bridge_data(decision, shot, prev_state), purpose=purpose)


def ensure_bridge_context(decision: dict, shot: dict, prev_state: dict) -> dict:
    out = dict(decision or {})
    if not isinstance(out.get("bridge_data"), dict):
        out["bridge_data"] = build_bridge_data(out, shot, prev_state)
    if not (out.get("bridge_context") or "").strip():
        out["bridge_context"] = render_bridge_context(out["bridge_data"], purpose="full")
    return out


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
    use_director = bool(needs_director_board(shot) and not use_tail)
    use_staging = (not use_director) and _needs_staging(
        shot, prev_state, scene_cut=scene_cut, overlap=overlap)
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
    elif use_director:
        reason = "本镜涉及复杂动作/运镜/多人调度，需要导演图先统一构图、运动路径与调度重点。"
        camera_hint = "以导演图作为构图蓝本：先建立动作路线和人物层次，再按本镜景别推进剧情重点。"
    else:
        who = "、".join(sorted(prev_chars & cur_chars))
        if use_staging:
            reason = f"同场景同人物（{who or '人物重合'}）存在空间/视线/位置关系，需站位图锁定轴线与相对距离。"
            camera_hint = ("切镜头但保持空间轴线：换景别/机位推进本镜，同时按站位图维持人物左右、视线方向与距离关系。")
        else:
            reason = "普通影视切镜头衔接：本镜重点是情绪/信息/景别推进，不需要额外桥接图强绑定。"
            camera_hint = ("切镜头衔接：换一个与上一镜不同的景别/机位，把观众注意力转到本镜动作或情绪重点。")
    return {
        "scene_cut": scene_cut,
        "use_tail_frame": use_tail,
        "use_staging": use_staging,
        "use_director_board": use_director,
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
    rule = normalize_decision(decide_handoff_rule(shot, prev_state))
    if not use_llm:
        return ensure_bridge_context(enforce_director_policy(rule, shot), shot, prev_state)
    try:
        body = tpl.get_template_body("handoff_decision")
        prompt = tpl.render(body, {
            "prev_state": _state_text(prev_state),
            "scene": shot.get("scene", ""),
            "characters": "、".join(sorted(_names(shot.get("characters")))) or "（无）",
            "props": "、".join(sorted(_names(shot.get("props")))) or "（无）",
            "action": shot.get("action", ""),
            "camera": shot.get("camera", ""),
            "handoff": shot.get("handoff", ""),
        })
        out = llm.chat_json([{"role": "user", "content": prompt}], model=model, temperature=0.2)
        if isinstance(out, dict):
            out = normalize_decision(out, rule)
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
            return ensure_bridge_context(enforce_director_policy(out, shot), shot, prev_state)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[Continuity] LLM 决策失败，回退规则: {e}")
    return ensure_bridge_context(enforce_director_policy(rule, shot), shot, prev_state)


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


def _stable_image_name(shot_no: str, suffix: str) -> str:
    safe = re.sub(r"[^0-9A-Za-z._-]+", "_", shot_no or "shot").strip("._-") or "shot"
    return f"{safe}_{suffix}.png"


def _overwrite_generated_image(out: dict, pid: str, shot_no: str, suffix: str) -> dict:
    target_name = _stable_image_name(shot_no, suffix)
    target = story_state.continuity_dir(pid) / target_name
    src = Path(out.get("filepath") or "")
    if src.exists() and src.resolve() != target.resolve():
        target.write_bytes(src.read_bytes())
        try:
            src.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
    out = dict(out)
    out["filename"] = target_name
    out["filepath"] = str(target)
    return out


def _same_scene(a: Optional[str], b: Optional[str]) -> bool:
    def norm(v):
        return str(v or "").lstrip("@#$").strip()
    return bool(norm(a)) and norm(a) == norm(b)


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
            "asset_id": m.get("asset_id"),
            "filename": fn,
        })
    items = reference_set.sort_for_shot(items, shot)
    logger.info(
        "[Continuity] %s canonical asset ref order: %s",
        shot.get("shot_no", "shot"),
        [f"{i + 1}:{it.get('role')}:{it.get('name')}" for i, it in enumerate(items)],
    )
    return [it["b64"] for it in reference_set.cap(items, _max_refs())]


def _scene_aerial_ref(pid: str, shot: dict, assets: Optional[list]) -> Optional[str]:
    """Use a scene aerial image as the staging diagram base map only.

    It is intentionally not returned by video reference assembly, so it will not
    receive an @图N number or be uploaded as a standalone video reference.
    """
    if not assets:
        return None
    scene = str(shot.get("scene") or "").lstrip("@#$").strip()
    if not scene:
        return None
    for asset in assets:
        if asset.get("type") != "scene":
            continue
        if str(asset.get("name") or "").strip() != scene:
            continue
        fn = asset.get("aerial_image")
        if not fn:
            return None
        path = PROJECTS_DIR / pid / "assets" / fn
        if not path.exists():
            return None
        return base64.b64encode(path.read_bytes()).decode("utf-8")
    return None


def generate_staging(pid: str, shot: dict, prev_state: dict, *,
                     style: str = "", model: Optional[str] = None,
                     size: str = "1024x1024", assets: Optional[list] = None,
                     bridge_context: str = "") -> dict:
    body = tpl.get_template_body("staging_diagram")
    aerial = _scene_aerial_ref(pid, shot, assets)
    prompt = tpl.render(body, {
        "style": style,
        "scene": shot.get("scene", ""),
        "characters": "、".join(sorted(_names(shot.get("characters")))) or "（无）",
        "prev_blocking": _state_text(prev_state),
        "bridge_context": bridge_context or build_bridge_context({}, shot, prev_state, purpose="staging"),
        "props": "、".join(sorted(_names(shot.get("props")))) or "（无）",
    })
    if aerial:
        prompt += "\n\n参考图说明：随附的场景鸟瞰图仅作为站位图底图/空间坐标参考，请在其空间关系上绘制本镜角色站位、视线方向、移动箭头与关键道具位置；不要把鸟瞰图当作最终视频画面风格或单独背景图。"
    timeout = int(settings_store.load_settings().get("image_timeout", 180) or 180)
    logger.info(
        "[Continuity] %s generating staging image: size=%s timeout=%ss",
        shot.get("shot_no", "shot"), size, timeout,
    )
    ref_images = []
    if aerial:
        ref_images.append(aerial)
        logger.info(
            "[Continuity] %s staging uses scene aerial image as base map",
            shot.get("shot_no", "shot"),
        )
    prev_staging = _read_b64(pid, (prev_state or {}).get("staging_image"))
    if prev_staging and _same_scene((prev_state or {}).get("scene"), shot.get("scene")):
        ref_images.append(prev_staging)
        logger.info(
            "[Continuity] %s staging references previous same-scene staging image",
            shot.get("shot_no", "shot"),
        )
    ref_images.extend(_shot_asset_refs(pid, shot, assets))
    out = image_gen.generate_image(
        prompt, model=model, size=size,
        ref_images_b64=ref_images,
        save_dir=story_state.continuity_dir(pid),
        filename_prefix=f"{(shot.get('shot_no') or 'shot')}_staging",
        timeout=timeout,
    )
    out = _overwrite_generated_image(out, pid, shot.get("shot_no", ""), "staging")
    story_state.set_staging(pid, shot.get("shot_no", ""), out["filename"])
    logger.info("[Continuity] %s staging image saved: %s", shot.get("shot_no", "shot"), out["filename"])
    return out


def generate_director_board(pid: str, shot: dict, prev_state: dict, *,
                            style: str = "", model: Optional[str] = None,
                            size: str = "1536x1024", assets: Optional[list] = None,
                            bridge_context: str = "") -> dict:
    body = tpl.get_template_body("director_board")
    prompt = tpl.render(body, {
        "style": style,
        "scene": shot.get("scene", ""),
        "characters": "、".join(sorted(_names(shot.get("characters")))) or "（无）",
        "props": "、".join(sorted(_names(shot.get("props")))) or "（无）",
        "prev_handoff": (prev_state or {}).get("director_note", "") or "（无上文接口）",
        "bridge_context": bridge_context or build_bridge_context({}, shot, prev_state, purpose="director"),
        "action": shot.get("action", ""),
    })
    timeout = int(settings_store.load_settings().get("image_timeout", 180) or 180)
    logger.info(
        "[Continuity] %s generating director board: size=%s timeout=%ss",
        shot.get("shot_no", "shot"), size, timeout,
    )
    out = image_gen.generate_image(
        prompt, model=model, size=size,
        ref_images_b64=_shot_asset_refs(pid, shot, assets),
        save_dir=story_state.continuity_dir(pid),
        filename_prefix=f"{(shot.get('shot_no') or 'shot')}_director",
        timeout=timeout,
    )
    out = _overwrite_generated_image(out, pid, shot.get("shot_no", ""), "director")
    story_state.set_director_board(pid, shot.get("shot_no", ""), out["filename"])
    logger.info("[Continuity] %s director board saved: %s", shot.get("shot_no", "shot"), out["filename"])
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
    out["pass"] = _bool(out.get("pass"))
    try:
        out["score"] = max(0, min(100, int(out.get("score", 0))))
    except (TypeError, ValueError):
        out["score"] = 0
    if not isinstance(out.get("issues"), list):
        out["issues"] = [str(out.get("issues"))] if out.get("issues") else []
    out["issues"] = [str(x).strip() for x in out["issues"] if str(x).strip()]
    out["suggestion"] = str(out.get("suggestion") or "").strip()
    out["source"] = "llm"
    out["image_count"] = len(images)
    story_state.set_review(pid, shot_no, out)
    return out
