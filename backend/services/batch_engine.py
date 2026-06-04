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
from . import llm

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
        chars = s.get("characters", []) or []
        tasks.append({
            "shot_no": s.get("shot_no", ""),
            "seq": s.get("seq"),
            "src_text": s.get("src_text", ""),
            "prompt": "，".join(prompt_parts),
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
            # per-shot duration from pacing (5字/秒，按情绪调)，供视频生成使用
            "duration": s.get("duration"),
            # 统一 @ 引用的展示字段（供视频【镜头动态】用，不影响 continuity 的原始字段）
            "scene_ref": _norm_refs(s.get("scene", ""), asset_list),
            "action_ref": _norm_refs(s.get("action", ""), asset_list),
            "handoff_ref": _norm_refs(s.get("handoff", ""), asset_list),
            "dialogue_ref": _norm_refs(s.get("dialogue", ""), asset_list),
            "characters_ref": [_norm_refs(c, asset_list) for c in chars],
        })
    return tasks


# 库内 #场景/$道具 仅作分类；喂给生成模型时所有引用统一 @。已注册资产由
# resolve_mentions 映射为 @名，未注册的残留 #x/$x 也统一改写为 @x。
_STRAY_TAG_RE = re.compile(r"[#$](?=[\w\u4e00-\u9fff])")


def _norm_refs(text: str, asset_list: list) -> str:
    if not text:
        return ""
    t = assets_core.resolve_mentions(text, asset_list).get("text", text)
    return _STRAY_TAG_RE.sub("@", t)


_CONSISTENCY_GUARD = (
    "镜头运动自然流畅、画面无跳帧；保持人物外形、服装与道具形制和参考图严格一致；"
    "物体保持刚性不变形/不融化、手与道具咬合自然、人物不随镜头平移滑步、重力方向真实、"
    "禁止多手多指/穿模；禁止字幕、禁止水印"
)

_LONG_TAKE_KW = ("长镜头", "一镜到底", "无缝", "不切", "连续运动")

# 四段式节奏：分段标签 + 时间权重（占总时长比例），与「起势/核心/转折/收束」对应。
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
    """衔接方式：默认切镜头/景别切换；仅当 handoff/camera 显式标注长镜头时才连续。"""
    blob = f"{task.get('handoff','')} {task.get('camera','')}"
    if any(k in blob for k in _LONG_TAKE_KW):
        return "长镜头无缝衔接，运动/构图/光线自上一镜自然延续"
    return "与上一镜切镜头/景别切换衔接，承接其人物状态与站位"


def _first_frame_anchor(task: dict) -> str:
    """首帧锚定（权重最高）：只描述起幅静帧——主体 + 所在场景，不堆叠动作过程
    （动作交给【动作过程·时间轴】，避免与首帧重复）。"""
    chars = _ff_chars(task)
    scene = _ff_scene(task)
    lead = chars or "主体"
    bits = [lead, scene and f"位于{scene}"]
    return "，".join(b for b in bits if b)


def _split_clauses(text: str) -> list[str]:
    """把动作文本切成小句：先按句末标点，必要时再按逗号细分。"""
    text = (text or "").strip()
    if not text:
        return []
    parts = [p for p in re.split(r"[。．\.!！?？;；\n]+", text) if p.strip("，、 　")]
    parts = [p.strip("，、 　") for p in parts]
    # 句子太少而文本较长：进一步按逗号切，便于按时间轴分配
    if len(parts) < 2 and len(text) > 24:
        parts = [p.strip() for p in re.split(r"[，,、]+", text) if p.strip()]
    return parts or [text]


def _eff_dur(task: dict) -> int:
    """本镜有效时长(秒)：取分镜估算值，缺失时回落到「单镜目标时长」设置，
    统一钳制到 [3,15]。保证每条视频提示词的时间轴都有确定的总时长可分配，
    消除「有的带时长、有的不带」的不一致。"""
    d = _as_int(task.get("duration"))
    if d <= 0:
        try:
            d = int(settings_store.load_settings().get("shot_target_seconds", 10))
        except (TypeError, ValueError):
            d = 10
    return max(3, min(15, d))


def _action_timeline(task: dict) -> str:
    """按目标时长把动作分配到时间轴小节，避免长镜头动作分布不均/时间浪费。
    规范化：任意时长都显式给出 0→总时长 的时间区间——短镜头(单段)写
    「0–N秒（连贯动作）：…」，长镜头(≥6s 且动作≥2小句)按四段式拆分铺满时长。"""
    action = _ff_action(task).rstrip("。．. ")
    if not action:
        return ""
    dur = _eff_dur(task)
    clauses = _split_clauses(action)
    if dur >= 6 and len(clauses) >= 2:
        n = 4 if dur >= 12 else (3 if dur >= 9 else 2)
        n = min(n, len(clauses))
    else:
        n = 1
    if n <= 1:
        # 单段也显式标注时间区间，铺满整条时长，保证格式统一、时长被充分利用
        return f"0–{dur}秒（连贯动作）：{action}"
    # 把小句尽量均匀分配到 n 段（前面的段先多拿一句）
    base, extra = divmod(len(clauses), n)
    per = [base + (1 if i < extra else 0) for i in range(n)]
    # 时间边界（按权重累加，末段对齐总时长）
    bounds, acc = [0], 0.0
    for w in _BEAT_WEIGHTS[n]:
        acc += w * dur
        bounds.append(int(round(acc)))
    bounds[-1] = dur
    for i in range(1, len(bounds)):  # 保证单调递增
        if bounds[i] <= bounds[i - 1]:
            bounds[i] = bounds[i - 1] + 1
    labels = _BEAT_LABELS[n]
    out, ci = [], 0
    for k in range(n):
        seg = clauses[ci:ci + per[k]]
        ci += per[k]
        out.append(f"{bounds[k]}–{bounds[k + 1]}秒（{labels[k]}）：{'，'.join(seg)}")
    return "；".join(out)


def _video_dynamics(task: dict) -> str:
    """确定性合成视频镜头动态（不调 LLM、零额度、可在模板/工作台编辑）。

    结构（分行、空字段自动省略、引用统一 @）：
      首帧锚定（静帧）→ 运镜 → 动作过程·时间轴 → 衔接/收尾 → 台词 → 节奏 → 防穿帮。
    时间轴按目标时长把动作拆成「起势/核心/转折/收束」小节，长镜头不再浪费时长、
    动作均匀分布。"""
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
    prev = (task.get("_prev_handoff") or "").strip()
    head = f"承接上一镜（{prev}）；{_transition_of(task)}" if prev else _transition_of(task)
    join = [f"【衔接】{head}"]
    ho = (task.get("handoff_ref") or task.get("handoff") or "").strip()
    if ho:
        join.append(f"本镜收尾：{ho}")
    lines.append("｜".join(join))
    dlg = (task.get("dialogue_ref") or task.get("dialogue") or "").strip()
    if dlg:
        lines.append(f"【台词/旁白】{dlg}（声画同步，不出字幕）")
    emo = (task.get("emotion") or "").strip()
    dur = _eff_dur(task)
    pace = [f"总时长约{dur}秒"]
    if emo:
        pace.append(f"{emo}情绪")
    pace.append("零删减、动作均匀铺满整条时长")
    lines.append("【节奏】" + "，".join(pace))
    lines.append("【防穿帮/一致性】" + _CONSISTENCY_GUARD)
    return "\n".join(lines)


def _video_prompt_vars(task: dict, image_prompt: str) -> dict:
    """Variables exposed to the editable `video_prompt` template. `image_prompt`
    is the resolved picture description; `dynamics` is the engine-composed motion
    block (empty fields auto-omitted); the rest are granular fields for power
    users who rewrite the template. 引用字段统一 @。"""
    dur = _eff_dur(task)
    return {
        "image_prompt": (image_prompt or "").rstrip("。，、 "),
        "dynamics": _video_dynamics(task),
        "first_frame": _first_frame_anchor(task),
        "action_timeline": _action_timeline(task),
        "transition": _transition_of(task),
        "handoff": (task.get("handoff_ref") or task.get("handoff") or "").strip(),
        "camera": (task.get("camera") or "").strip(),
        "action": _ff_action(task),
        "emotion": (task.get("emotion") or "").strip(),
        "duration": dur,
        "dialogue": (task.get("dialogue_ref") or task.get("dialogue") or "").strip(),
        "scene": _ff_scene(task),
        "characters": _ff_chars(task),
        "props": "、".join(task.get("props", []) or []),
        "consistency": _CONSISTENCY_GUARD,
    }


def build_video_prompt(task: dict, image_prompt: str) -> str:
    """Render the active `video_prompt` template for one shot/task."""
    body = prompt_templates.get_template_body("video_prompt")
    vid = prompt_templates.render(body, _video_prompt_vars(task, image_prompt)).strip()
    return vid or (image_prompt or "")


def _preview_definition_block(pid: Optional[str], task: dict,
                              kind: str = "video") -> str:
    """工作台预览用【画面定义】块：复用与「实际生成」完全相同的 _asset_ref_items +
    cap 排序逻辑（含场景鸟瞰图 scene_aerial 注入、相同优先级裁剪），仅不读 b64。
    这样预览里 @图N 的编号/对象与点生成后真正发送的参考图数组逐位一致，杜绝错乱。

    生成时由 compose_prompt 以权威定义（另含运行期尾帧/导演图/站位图等仅运行期可知
    的图）覆盖，且幂等不重复堆叠。"""
    items = _asset_ref_items(pid, task, with_b64=False)
    kept = reference_set.cap(items, _max_refs(), require_b64=False)
    if not kept:
        return ""
    return reference_set.build_definition_block(kept, kind, require_b64=False)


def _with_preview_def(pid: Optional[str], task: dict, video_prompt: str,
                      kind: str = "video") -> str:
    """Prepend the preview【画面定义】block to *video_prompt* (idempotent)."""
    body = reference_set.strip_definition_block(video_prompt or "")
    head = _preview_definition_block(pid, task, kind)
    if not head:
        return body
    return head + "\n\n" + body if body else head


def build_shot_prompts(project: dict, shot_nos: Optional[list] = None) -> dict:
    """Preview the per-shot generation prompts WITHOUT generating anything.

    Returns ``{shot_no: {"image": str, "video": str}}`` using the same engine
    logic as real generation (style + camera + resolved @/#/$ shot text), so the
    worktable can show editable, what-you-see-is-what-gets-generated prompts. The
    video variant is rendered from the user-editable `video_prompt` template
    (default = image description + engine-composed 【镜头动态】).
    """
    pid = project.get("_pid") or project.get("id")
    want = {str(n) for n in shot_nos} if shot_nos else None
    out: dict = {}
    prev_ho = ""  # 上一镜的收尾接口(handoff)，用于本镜首帧承接 → 整体联动
    for t in build_tasks_from_shots(project):  # 全量按序，保证联动正确
        no = t.get("shot_no", "")
        if prev_ho:
            t["_prev_handoff"] = prev_ho
        if want is None or str(no) in want:
            img = t.get("prompt", "") or ""
            out[no] = {"image": img,
                       "video": _with_preview_def(pid, t, build_video_prompt(t, img))}
        prev_ho = (t.get("handoff_ref") or t.get("handoff") or "").strip()
    return out


_ASSET_TYPE_LABEL = {"character": "角色", "scene": "场景", "prop": "道具"}


def _assets_desc_for_task(project: dict, task: dict) -> str:
    """本镜关联资产说明（喂给逐镜 LLM 推理）：'@名（类型）：描述／外观' 多行。"""
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
    """点「AI推理」时调用：用文本 LLM 对单个分镜推理出干净规范的图片+视频提示词。

    打通的上下文：①连续性——上一分镜的 handoff(收尾/站位/情绪) → 本镜承接；
    ②本镜关联资产的说明文本（@角色/#场景/$道具 → 名称+描述+外观）；③本镜结构字段
    与目标时长。剔除非画面元素（心理活动/旁白叙述等）。任何 LLM 异常回落到确定性合成。
    """
    pid = project.get("_pid") or project.get("id")
    target, prev_ho = None, ""
    for t in build_tasks_from_shots(project):  # 全量按序，取得上一镜 handoff
        if str(t.get("shot_no")) == str(shot_no):
            target = t
            break
        prev_ho = (t.get("handoff_ref") or t.get("handoff") or "").strip()
    if target is None:
        raise ValueError(f"分镜 {shot_no} 不存在")
    if prev_ho:
        target["_prev_handoff"] = prev_ho
    img_draft = target.get("prompt", "") or ""
    bible = project.get("story_bible") or {}
    variables = {
        "style": bible.get("style") or "",
        "shot_no": target.get("shot_no", ""),
        "duration": _eff_dur(target),
        "camera": target.get("camera", ""),
        "scene": _ff_scene(target),
        "characters": _ff_chars(target),
        "props": "、".join(target.get("props", []) or []),
        "action": _ff_action(target),
        "emotion": (target.get("emotion") or "").strip(),
        "dialogue": (target.get("dialogue_ref") or target.get("dialogue") or "").strip(),
        "prev_handoff": prev_ho or "（本镜为首镜，无上文）",
        "handoff": (target.get("handoff_ref") or target.get("handoff") or "").strip(),
        "assets_desc": _assets_desc_for_task(project, target),
        "consistency": _CONSISTENCY_GUARD,
    }
    try:
        body = prompt_templates.get_template_body("shot_prompt_infer")
        # 留足额度给「推理型」LLM（reasoning_content 单独占用 max_tokens）：1400 会被
        # 思维链吃光导致 content 截断成空串/坏 JSON（实测 deepseek 推理模型）。给 6000。
        data = llm.chat_json(
            [{"role": "user", "content": prompt_templates.render(body, variables)}],
            model=model, temperature=0.5, max_tokens=6000,
        )
        image = (data.get("image") or "").strip() if isinstance(data, dict) else ""
        video = (data.get("video") or "").strip() if isinstance(data, dict) else ""
        if not (image or video):
            raise llm.LLMError("LLM 返回空结果")
        vid = video or build_video_prompt(target, image or img_draft)
        return {
            "image": image or img_draft,
            "video": _with_preview_def(pid, target, vid),
            "source": "llm",
        }
    except Exception as e:  # noqa: BLE001 — 任意异常都回落到确定性合成，保证可用
        logger.warning("infer_shot_prompt 回落确定性合成 (%s): %s", shot_no, e)
        return {
            "image": img_draft,
            "video": _with_preview_def(pid, target,
                                       build_video_prompt(target, img_draft)),
            "source": "fallback",
            "error": str(e),
        }


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


def _asset_ref_items(pid: str, task: dict, with_b64: bool = True) -> list:
    """Reference items for a task's @/#/$ asset materials (those with a ref image).

    场景资产若另有「鸟瞰图」(aerial_image)，紧随其主场景图一并作为参考喂入，用于
    固定场景空间位置与机位布局（角色 scene_aerial，优先级紧随主场景图之后）。

    with_b64=True（生成）：读取并附带 base64，作为实际发送的参考图数组。
    with_b64=False（预览）：只做存在性校验并产出同序的元数据（role/name/asset_id），
    供工作台【画面定义】@图N 与实际发送数组逐位对齐用，避免读盘开销。"""
    if not pid:
        return []
    asset_dir = PROJECTS_DIR / pid / "assets"
    by_id = {a.get("id"): a for a in assets_core.list_assets(pid)}
    items = []
    for m in task.get("materials", []) or []:
        fn = m.get("ref_image")
        if not fn:
            continue
        path = asset_dir / fn
        if not path.exists():
            continue
        it = {
            "role": reference_set.material_role(m),
            "name": m.get("name"),
            "asset_id": m.get("asset_id"),
            "old_ph": m.get("placeholder"),
        }
        if with_b64:
            it["b64"] = base64.b64encode(path.read_bytes()).decode("utf-8")
        items.append(it)
        asset = by_id.get(m.get("asset_id")) or {}
        aerial_fn = asset.get("aerial_image")
        if aerial_fn:
            aerial_path = asset_dir / aerial_fn
            if aerial_path.exists():
                ait = {
                    "role": "scene_aerial",
                    "name": m.get("name"),
                    "asset_id": m.get("asset_id"),
                }
                if with_b64:
                    ait["b64"] = base64.b64encode(
                        aerial_path.read_bytes()).decode("utf-8")
                items.append(ait)
    return items


def _finalize_refs(task: dict, items: list, kind: str = "video") -> tuple[list, str]:
    """Cap *items* by priority and build the unified-@ reference prompt.
    Returns (b64s, prompt)."""
    kept = reference_set.cap(items, _max_refs())
    prompt = reference_set.compose_prompt(
        task.get("prompt", ""), task.get("materials", []), kept, kind=kind)
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
    # 默认直接开启「LLM 升级决策」：除非显式传 decision_llm=false，否则衔接判定走真·LLM
    # 升级（无凭据/失败时 decide_handoff 自动回退确定性规则，不阻断）。
    use_llm = bool(params.get("decision_llm", True))
    decision = continuity.decide_handoff(shot, prev_state, use_llm=use_llm,
                                         model=params.get("llm_model"))
    logger.info(
        f"[Continuity] {shot_no} 衔接决策[{decision.get('source', 'rule')}]: "
        f"切场={decision.get('scene_cut')} 尾帧={decision.get('use_tail_frame')} "
        f"站位图={decision.get('use_staging')} 导演图={decision.get('use_director_board')} "
        f"长镜头={decision.get('long_take')}｜{decision.get('reason', '')}")

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
    batches.update_task(pid, batch["id"], task["id"], {
        "decision": decision,
        "continuity_strategy": decision.get("strategy"),
        "continuity_source": decision.get("source"),
        "src_text": task.get("src_text", ""),
    })

    # 准确执行决策的连续性机制：若决策需要站位图/导演图且本镜尚未生成，则按需即时生成
    # （best-effort：无生图凭据/失败时记日志、不阻断本镜视频生成）。这样 LLM 升级判定
    # 出的策略会真正落地，而非仅写进决策却无图可用。
    if decision.get("use_staging") or decision.get("use_director_board"):
        snap0 = story_state.get_shot_state(pid, shot_no) or {}
        proj_assets = assets_core.list_assets(pid)
        if decision.get("use_staging") and not snap0.get("staging_image"):
            try:
                continuity.generate_staging(pid, shot, prev_state, style=style,
                                            assets=proj_assets)
                logger.info(f"[Continuity] {shot_no} 按决策生成站位图（锁定人物站位/构图）")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[Continuity] {shot_no} 站位图生成失败（不阻断）: {e}")
        if decision.get("use_director_board") and not snap0.get("director_board"):
            try:
                continuity.generate_director_board(pid, shot, prev_state, style=style,
                                                   assets=proj_assets)
                logger.info(f"[Continuity] {shot_no} 按决策生成导演图（构图蓝本）")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[Continuity] {shot_no} 导演图生成失败（不阻断）: {e}")

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
    # Video generation is paid and content-sensitive: do not auto-resubmit a
    # failed video prompt, even when the provider/error classifier marks the
    # failure as retryable. The UI will surface the error and let the user edit
    # the prompt/settings before manually generating again.
    max_attempts = 1 if batch.get("kind") == "video" else max(1, task.get("max_attempts", 3))
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
