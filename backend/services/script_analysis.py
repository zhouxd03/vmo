"""Two-stage LLM script analysis.

Stage 1 (global): read the whole script -> StoryBible (anchors only).
Stage 2 (per-batch): split segments into token-bounded chunks; decompose each
chunk into structured shots, carrying the StoryBible + previous chunk's handoff
summary forward (sliding window) so continuity survives across chunk borders.

Designed for tens-of-thousands of characters: chunking avoids OOM / context
blowup; each chunk can be retried independently (resume-from-checkpoint).
"""

import json
import logging
import re
from typing import Any, Callable, Optional

from ..core import prompt_templates as tpl
from ..core import settings as settings_store
from . import llm, pacing

logger = logging.getLogger("batch_studio")

# Rough char budget per stage-2 chunk (relay token limits vary; keep modest).
DEFAULT_CHUNK_CHARS = 2800
# For stage-1, if the full text is huge, condense by sampling head/middle/tail.
STAGE1_MAX_CHARS = 24000
_SCENE_TIME_WORDS = (
    "凌晨", "清晨", "早晨", "上午", "中午", "午后", "下午", "傍晚", "黄昏",
    "晚上", "夜晚", "深夜", "午夜", "黎明", "雨夜", "雪夜", "白天", "夜间",
)
_SCENE_TIME_RE = re.compile(
    r"(?:^|[_\-·｜|、\s])(" + "|".join(map(re.escape, _SCENE_TIME_WORDS)) + r")(?:$|[_\-·｜|、\s])"
)
_SCENE_TIME_PREFIX_RE = re.compile(r"^(" + "|".join(map(re.escape, _SCENE_TIME_WORDS)) + r")")
_SCENE_TIME_SUFFIX_RE = re.compile(r"(" + "|".join(map(re.escape, _SCENE_TIME_WORDS)) + r")$")


def _condense_for_global(text: str, segments: list[dict]) -> str:
    if len(text) <= STAGE1_MAX_CHARS:
        return text
    # Sample evenly across segments to preserve whole-arc coverage.
    joined = "\n".join(f"[{s.get('seq')}] {s.get('text','')}" for s in segments)
    if len(joined) <= STAGE1_MAX_CHARS:
        return joined
    head = joined[: STAGE1_MAX_CHARS // 2]
    tail = joined[-STAGE1_MAX_CHARS // 2:]
    return head + "\n…（中段省略，仅做全局锚定）…\n" + tail


def run_global_analysis(
    project: dict,
    *,
    model: Optional[str] = None,
) -> dict:
    """Stage 1 -> StoryBible dict."""
    text = project.get("raw_text", "")
    segments = project.get("segments", [])
    body = tpl.get_template_body("global_analysis")
    prompt = tpl.render(body, {
        "source_type": project.get("source_type", "txt"),
        "full_text": _condense_for_global(text, segments),
    })
    logger.info(f"[Analysis] Stage1 全局分析: {len(text)} 字 -> StoryBible")
    bible = llm.chat_json(
        [{"role": "user", "content": prompt}],
        model=model,
        temperature=0.4,
    )
    if not isinstance(bible, dict):
        raise llm.LLMError("全局分析未返回 JSON 对象")
    bible = _normalize_bible(bible)
    return bible


def _as_list(v) -> list:
    if isinstance(v, list):
        return v
    if v in (None, ""):
        return []
    return [v]


def _normalize_bible(bible: dict) -> dict:
    out = dict(bible or {})
    for key in ("title", "logline", "style", "summary"):
        out[key] = str(out.get(key) or "")
    for key in ("characters", "scenes", "props"):
        items = []
        for it in _as_list(out.get(key)):
            if isinstance(it, dict):
                item = dict(it)
                item["name"] = str(item.get("name") or "").strip()
                if item["name"]:
                    items.append(item)
            elif str(it).strip():
                items.append({"name": str(it).strip()})
        out[key] = items
    out["scenes"] = _normalize_scenes(out.get("scenes") or [])
    out["continuity_constraints"] = [
        str(x).strip() for x in _as_list(out.get("continuity_constraints")) if str(x).strip()
    ]
    if not (out["characters"] or out["scenes"] or out["props"] or out.get("style")):
        raise llm.LLMError("全局分析结果缺少人物/场景/道具/风格等有效信息，请换模型或调整原文后重试")
    return out


def _clean_scene_name(name: str) -> tuple[str, list[str]]:
    raw = str(name or "").strip().lstrip("#").strip()
    found: list[str] = []
    for word in _SCENE_TIME_WORDS:
        if word in raw:
            found.append(word)
    s = raw
    s = _SCENE_TIME_RE.sub(" ", s)
    s = _SCENE_TIME_PREFIX_RE.sub("", s)
    s = _SCENE_TIME_SUFFIX_RE.sub("", s)
    s = re.sub(r"[_\-·｜|、\s]+", "", s).strip()
    return (s or raw), list(dict.fromkeys(found))


def _normalize_scenes(scenes: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        item = dict(scene)
        base_name, time_words = _clean_scene_name(item.get("name") or "")
        item["name"] = base_name
        desc = str(item.get("desc") or "").strip()
        if time_words:
            note = "可变时段/光线条件：" + "、".join(time_words)
            if note not in desc:
                desc = (desc + "；" + note).strip("；")
        item["desc"] = desc
        cur = merged.get(base_name)
        if not cur:
            merged[base_name] = item
            continue
        for key in ("desc", "note"):
            extra = str(item.get(key) or "").strip()
            if extra and extra not in str(cur.get(key) or ""):
                cur[key] = (str(cur.get(key) or "").rstrip("；") + "；" + extra).strip("；")
    return list(merged.values())


def _clean_text(v) -> str:
    return str(v or "").strip()


def _normalize_names(v) -> list[str]:
    raw = v if isinstance(v, list) else ([v] if v else [])
    return [_clean_text(x) for x in raw if _clean_text(x)]


def _normalize_shot(raw: dict, *, chunk_seqs: list, shot_idx: int) -> dict:
    if not isinstance(raw, dict):
        raise llm.LLMError(f"第 {shot_idx + 1} 个分镜不是 JSON 对象")
    sh = dict(raw)
    sh["scene"] = _clean_text(sh.get("scene"))
    if sh["scene"]:
        prefix = "#" if sh["scene"].lstrip().startswith("#") else ""
        scene_name, _time_words = _clean_scene_name(sh["scene"])
        sh["scene"] = prefix + scene_name if scene_name else sh["scene"]
    sh["characters"] = _normalize_names(sh.get("characters"))
    sh["props"] = _normalize_names(sh.get("props"))
    sh["action"] = _clean_text(sh.get("action"))
    sh["camera"] = _clean_text(sh.get("camera"))
    sh["dialogue"] = _clean_text(sh.get("dialogue"))
    sh["emotion"] = _clean_text(sh.get("emotion") or sh.get("mood"))
    sh["key_elements"] = _clean_text(sh.get("key_elements"))
    sh["handoff"] = _clean_text(sh.get("handoff"))
    if not (sh["action"] or sh["dialogue"] or sh["scene"]):
        raise llm.LLMError(f"第 {shot_idx + 1} 个分镜缺少 scene/action/dialogue，无法确定画面内容")
    sh["seq"] = _coerce_int(sh.get("seq"))
    for end_key in ("seq_end", "end_seq", "src_seq_end"):
        if end_key in sh:
            sh[end_key] = _coerce_int(sh.get(end_key))
    if sh.get("seq") is None and chunk_seqs:
        sh["seq"] = chunk_seqs[min(shot_idx, len(chunk_seqs) - 1)]
    return sh


def _entity_name(x) -> str:
    return (x.get("name") if isinstance(x, dict) else str(x)) or ""


def merge_bible(base: Optional[dict], add: dict) -> dict:
    """Union a per-episode analysis into the novel-level shared StoryBible.

    Entities are de-duplicated by name so a character/scene/prop introduced in
    one episode is not duplicated when later episodes are analysed.
    """
    base = base or {"characters": [], "scenes": [], "props": [],
                    "continuity_constraints": []}
    for key in ("characters", "scenes", "props"):
        have = {_entity_name(e).strip() for e in base.get(key, [])}
        for e in add.get(key, []) or []:
            nm = _entity_name(e).strip()
            if nm and nm not in have:
                base.setdefault(key, []).append(e)
                have.add(nm)
    cc = base.setdefault("continuity_constraints", [])
    for c in add.get("continuity_constraints", []) or []:
        if c not in cc:
            cc.append(c)
    # Novel-level scalar fields: keep the first non-empty value so they survive
    # the per-episode merge (previously title/logline/style were dropped here,
    # leaving the StoryBible header showing "—").
    for scalar in ("title", "logline", "style", "summary"):
        if add.get(scalar) and not base.get(scalar):
            base[scalar] = add[scalar]
    return base


def _chunk_segments(segments: list[dict], chunk_chars: int) -> list[list[dict]]:
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    size = 0
    for seg in segments:
        t = seg.get("text", "")
        if cur and size + len(t) > chunk_chars:
            chunks.append(cur)
            cur = []
            size = 0
        cur.append(seg)
        size += len(t)
    if cur:
        chunks.append(cur)
    return chunks


def _segment_prompt_line(seg: dict) -> str:
    text = seg.get("text", "")
    marker_type = seg.get("marker_type")
    marker_no = _coerce_int(seg.get("marker_no"))
    marker = ""
    if marker_type == "shot" and marker_no is not None:
        marker = f" <<<分镜标记{marker_no}>>>"
    elif marker_type == "episode" and marker_no is not None:
        marker = f" <<<分集标记{marker_no}>>>"
    return f"[{seg.get('seq')}]{marker} {text}"


def _relevant(items: list[dict], chunk_text: str, prev_handoff: str) -> list[dict]:
    """Keep only entities actually referenced in this chunk (or carried over via
    the previous handoff). Prevents dumping the whole novel-level cast/scene/prop
    list into every chunk — which on later episodes piles up and overloads the
    model (信息过载/内容堆叠). Cross-episode sharing is preserved: an entity reused
    in a later chunk still matches by name."""
    hay = (chunk_text or "") + "\n" + (prev_handoff or "")
    out = [e for e in items if e.get("name") and e["name"] in hay]
    return out


def _coerce_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(str(v).strip())
        except (TypeError, ValueError):
            return None


def _resolve_src_seq(raw, seq_set: set, chunk_seqs: list, shot_idx: int, n_shots: int):
    """Map a shot to the source segment it derives from.

    Trusts the LLM's ``seq`` when it points at a real segment in this chunk;
    otherwise distributes the shot evenly across the chunk's segments by its
    ordinal position. This fixes the old behaviour where every shot in a chunk
    defaulted to the *first* segment's seq, so the worktable「原文」showed an
    unrelated/header paragraph for most shots (拆解后原文与分镜信息不符).
    """
    n = _coerce_int(raw)
    if n is not None and n in seq_set:
        return n
    if not chunk_seqs:
        return n
    if n_shots <= 1:
        return chunk_seqs[0]
    pos = round(shot_idx / (n_shots - 1) * (len(chunk_seqs) - 1))
    pos = max(0, min(len(chunk_seqs) - 1, pos))
    return chunk_seqs[pos]


def _seq_index(chunk_seqs: list) -> dict:
    return {seq: i for i, seq in enumerate(chunk_seqs)}


def _src_text_for_seqs(chunk: list[dict], seqs: list) -> str:
    wanted = set(seqs or [])
    return "\n".join(
        s.get("text") or "" for s in chunk
        if s.get("seq") in wanted and (s.get("text") or "").strip()
    ).strip()


def _shot_marker_index(chunk: list[dict]) -> dict[int, dict]:
    return {
        int(s["marker_no"]): s
        for s in chunk
        if s.get("marker_type") == "shot" and _coerce_int(s.get("marker_no")) is not None
    }


def _shot_marker_source(shot: dict, chunk: list[dict], shot_idx: int) -> tuple | None:
    """Prefer explicit <<<分镜标记N>>> blocks over fuzzy source matching."""
    index = _shot_marker_index(chunk)
    if not index:
        return None
    wanted = _coerce_int(shot.get("marker_no") or shot.get("shot_marker"))
    if wanted in index:
        seg = index[wanted]
    else:
        seq = _coerce_int(shot.get("seq"))
        seg = next(
            (
                s for s in chunk
                if s.get("seq") == seq and s.get("marker_type") == "shot"
            ),
            None,
        )
    if seg is None:
        shot_markers = [s for s in chunk if s.get("marker_type") == "shot"]
        if 0 <= shot_idx < len(shot_markers):
            seg = shot_markers[shot_idx]
        else:
            return None
    seq = seg.get("seq")
    marker_no = _coerce_int(seg.get("marker_no"))
    return seq, [seq] if seq is not None else [], seg.get("text", "").strip(), True, marker_no


def _contiguous_seq_range(chunk_seqs: list, start, end=None) -> list:
    if not chunk_seqs:
        return []
    idx = _seq_index(chunk_seqs)
    if start not in idx:
        return []
    si = idx[start]
    ei = idx.get(end, si) if end is not None else si
    if ei < si:
        ei = si
    return chunk_seqs[si:ei + 1]


_MATCH_PUNCT_RE = re.compile(r"[\s，。、；：！？“”‘’（）()\[\]【】…—\-·~,.!?;:\"'`*#@$]+")


def _norm_match(s: str) -> str:
    return _MATCH_PUNCT_RE.sub("", s or "")


def _bigrams(s: str) -> set:
    if len(s) >= 2:
        return {s[i:i + 2] for i in range(len(s) - 1)}
    return {s} if s else set()


def _coverage(shot_t: str, seg_t: str) -> float:
    """Fraction of the *shot* text's bigrams that appear in the segment.

    Asymmetric on purpose: a short narrative shot fully contained in a longer
    source paragraph should score ~1.0 even though the paragraph has extra
    bigrams. This is what lets us pick the paragraph the shot actually derives
    from instead of an unrelated scene-slug line.
    """
    a, b = _bigrams(shot_t), _bigrams(seg_t)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a)


def _match_source_range(shot: dict, chunk: list[dict]) -> tuple:
    """Locate the contiguous source segment range a shot derives from.

    A shot may legitimately merge several source paragraphs/subtitles. Returning
    only one seq makes the worktable "原文" look truncated, so we score short
    contiguous windows and keep the smallest confident range.
    """
    core = (shot.get("action") or "") + (shot.get("dialogue") or "")
    target = _norm_match(core) or _norm_match(shot.get("scene") or "")
    if not target:
        return [], ""
    best: tuple[float, int, int] | None = None
    max_window = min(8, len(chunk))
    for i in range(len(chunk)):
        combined = ""
        for j in range(i, min(len(chunk), i + max_window)):
            combined = (combined + "\n" + (chunk[j].get("text") or "")).strip()
            raw_score = _coverage(target, _norm_match(combined))
            # Prefer compact ranges when coverage is otherwise similar.
            score = raw_score - max(0, j - i) * 0.015
            if best is None or score > best[0]:
                best = (score, i, j)
    if not best or best[0] < 0.18:
        return [], ""
    _, i, j = best
    seqs = [s.get("seq") for s in chunk[i:j + 1] if s.get("seq") is not None]
    return seqs, _src_text_for_seqs(chunk, seqs)


def _source_span_from_shot(shot: dict, chunk: list[dict], chunk_seqs: list,
                           seq_set: set, shot_idx: int, n_shots: int) -> tuple:
    """Return (start_seq, src_seq_list, src_text, is_content_match)."""
    marker_source = _shot_marker_source(shot, chunk, shot_idx)
    if marker_source:
        return marker_source

    matched_seqs, matched_text = _match_source_range(shot, chunk)
    if matched_seqs and matched_text:
        return matched_seqs[0], matched_seqs, matched_text, True, None

    start = _resolve_src_seq(shot.get("seq"), seq_set, chunk_seqs, shot_idx, n_shots)
    raw_end = shot.get("seq_end") or shot.get("end_seq") or shot.get("src_seq_end")
    end = _coerce_int(raw_end)
    if end is not None and end in seq_set:
        seqs = _contiguous_seq_range(chunk_seqs, start, end)
    else:
        seqs = [start] if start is not None else []
    return start, seqs, _src_text_for_seqs(chunk, seqs), False, None


def _fill_missing_source_ranges(shots_in_chunk: list[dict], chunk: list[dict],
                                chunk_seqs: list) -> None:
    """Expand weak single-seq fallbacks up to the next shot start when needed."""
    if not chunk_seqs:
        return
    idx = _seq_index(chunk_seqs)
    starts = [sh.get("seq") for sh in shots_in_chunk]
    for i, sh in enumerate(shots_in_chunk):
        cur = sh.get("src_seq") or []
        if sh.pop("_src_content_match", False):
            continue
        if len(cur) != 1 or cur[0] not in idx:
            continue
        next_start = None
        for cand in starts[i + 1:]:
            if cand in idx and idx[cand] > idx[cur[0]]:
                next_start = cand
                break
        if next_start is None:
            continue
        expanded = chunk_seqs[idx[cur[0]]:idx[next_start]]
        if len(expanded) > len(cur):
            sh["src_seq"] = expanded
            sh["src_text"] = _src_text_for_seqs(chunk, expanded)


def _as_duration(v, fallback: int) -> int:
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return fallback


def _strip_trigger(name: str) -> str:
    return str(name or "").strip().lstrip("@#$").strip()


def _same_scene(a: dict, b: dict) -> bool:
    sa = _strip_trigger(a.get("scene"))
    sb = _strip_trigger(b.get("scene"))
    return bool(sa and sb and sa == sb)


def _merge_names(a, b) -> list[str]:
    out: list[str] = []
    for item in _as_list(a) + _as_list(b):
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _merge_seq_values(a, b) -> list:
    out: list = []
    for item in _as_list(a) + _as_list(b):
        if item is not None and item not in out:
            out.append(item)
    return out


def _seqs_are_adjacent(a: dict, b: dict) -> bool:
    left = [x for x in _as_list(a.get("src_seq")) if x is not None]
    right = [x for x in _as_list(b.get("src_seq")) if x is not None]
    if not left or not right:
        return True
    try:
        return int(right[0]) <= int(left[-1]) + 1
    except (TypeError, ValueError):
        return True


def _is_hard_cut_boundary(a: dict, b: dict) -> bool:
    text = "\n".join([
        str(a.get("camera") or ""),
        str(a.get("handoff") or ""),
        str(b.get("camera") or ""),
        str(b.get("action") or ""),
    ])
    hard_words = (
        "转场", "闪回", "梦境", "时间跳跃", "多年后", "与此同时", "另一边",
        "切至", "切到", "场景转换", "时空", "强切", "黑场",
    )
    if any(w in text for w in hard_words):
        return True
    ma = a.get("src_marker")
    mb = b.get("src_marker")
    return bool(ma and mb and ma != mb)


def _merge_shots_for_pacing(shots_in_chunk: list[dict], shot_target: int) -> list[dict]:
    """Merge obviously tiny neighboring shots so each shot can fill target time.

    LLMs often over-segment dialogue beats or micro-actions. For a 15s target,
    a 5-7s shot usually does not contain enough dramatic material for a full
    generated clip. This pass only merges safe neighbors: same scene, adjacent
    source text, no explicit hard cut / marker boundary.
    """
    if len(shots_in_chunk) < 2:
        return shots_in_chunk
    min_useful = max(pacing.MIN_SEC + 1, int(round(shot_target * 0.65)))
    desired = max(pacing.MIN_SEC, min(pacing.MAX_SEC, int(shot_target)))
    out: list[dict] = []
    i = 0
    while i < len(shots_in_chunk):
        cur = dict(shots_in_chunk[i])
        while i + 1 < len(shots_in_chunk):
            nxt = shots_in_chunk[i + 1]
            cur_dur = _as_duration(cur.get("duration"), desired)
            nxt_dur = _as_duration(nxt.get("duration"), desired)
            if cur_dur >= min_useful:
                break
            if cur_dur + nxt_dur > pacing.MAX_SEC:
                break
            if not _same_scene(cur, nxt):
                break
            if not _seqs_are_adjacent(cur, nxt):
                break
            if _is_hard_cut_boundary(cur, nxt):
                break
            cur["characters"] = _merge_names(cur.get("characters"), nxt.get("characters"))
            cur["props"] = _merge_names(cur.get("props"), nxt.get("props"))
            cur["action"] = "；".join(x for x in [cur.get("action"), nxt.get("action")] if x)
            cur["dialogue"] = "；".join(x for x in [cur.get("dialogue"), nxt.get("dialogue")] if x)
            cur["emotion"] = cur.get("emotion") or nxt.get("emotion")
            cur["key_elements"] = "；".join(x for x in [cur.get("key_elements"), nxt.get("key_elements")] if x)
            cur["handoff"] = nxt.get("handoff") or cur.get("handoff")
            cur["duration"] = min(pacing.MAX_SEC, max(desired, cur_dur + nxt_dur))
            cur["src_seq"] = _merge_seq_values(cur.get("src_seq"), nxt.get("src_seq"))
            cur["src_text"] = "\n".join(x for x in [cur.get("src_text"), nxt.get("src_text")] if x)
            if nxt.get("seq_end") is not None:
                cur["seq_end"] = nxt.get("seq_end")
            i += 1
        out.append(cur)
        i += 1
    if len(out) != len(shots_in_chunk):
        logger.info(
            "[Analysis] Stage2 pacing merge: %d -> %d shots (target=%ss)",
            len(shots_in_chunk), len(out), shot_target,
        )
    return out


def _normalize_duration_for_target(shot: dict, shot_target: int) -> None:
    target = max(pacing.MIN_SEC, min(pacing.MAX_SEC, int(shot_target or pacing.MAX_SEC)))
    current = _as_duration(shot.get("duration"), target)
    if current < int(round(target * 0.75)):
        estimated = pacing.estimate_shot_seconds(
            shot.get("dialogue", ""), shot.get("action", ""),
            shot.get("emotion") or shot.get("mood") or "",
            target_seconds=target)
        shot["duration"] = max(current, estimated, int(round(target * 0.75)))
    else:
        shot["duration"] = max(pacing.MIN_SEC, min(pacing.MAX_SEC, current))


def _bible_summary(bible: dict, chunk_text: str = "", prev_handoff: str = "") -> str:
    """Compact StoryBible for stage-2 context (keep tokens small).

    When *chunk_text* is given, the cast/scene/prop lists are filtered down to
    the entities relevant to this chunk so multi-episode runs don't accumulate
    an ever-growing, irrelevant entity dump (item: 剔除与本集无关元素、避免堆叠).
    """
    def names(items, key="name"):
        return "、".join(i.get(key, "") for i in items if i.get(key))

    chars = bible.get("characters", []) or []
    scenes = bible.get("scenes", []) or []
    props = bible.get("props", []) or []
    if chunk_text:
        chars = _relevant(chars, chunk_text, prev_handoff) or chars[:6]
        scenes = _relevant(scenes, chunk_text, prev_handoff) or scenes[:4]
        props = _relevant(props, chunk_text, prev_handoff)
    parts = [
        f"风格: {bible.get('style','')}",
        f"人物: {names(chars)}",
        f"场景: {names(scenes)}",
        f"道具: {names(props)}",
    ]
    cons = bible.get("continuity_constraints", [])
    if cons:
        parts.append("连续性约束: " + "；".join(cons))
    return "\n".join(parts)


def run_decompose(
    project: dict,
    *,
    model: Optional[str] = None,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    on_progress: Optional[Callable[[int, int], None]] = None,
    start_chunk: int = 0,
    existing_shots: Optional[list] = None,
    episode_no: int = 1,
    prev_handoff_init: Optional[str] = None,
    template_preset: Optional[str] = None,
) -> dict:
    """Stage 2 (自动) -> {"shots": [...], "blocks": [...]}.

    Resumable: start_chunk + existing_shots let a failed run continue.
    *template_preset* selects a 分镜模式 (batch_decompose preset) for this run.
    Always LLM-driven: llm.chat_json raises a clear credential error when no
    LLM 中转站 is configured (强制 LLM 介入, no silent deterministic fallback).
    """
    bible = project.get("story_bible") or {}
    segments = project.get("segments", [])
    chunks = _chunk_segments(segments, chunk_chars)
    total = len(chunks)
    body = tpl.get_template_body("batch_decompose", template_preset)
    # 「单镜目标时长」(设置) → 单镜承载字数（纯画面≈8字/秒）。拆解时按此控制每镜体量；
    # 对白多的镜由模板按≈5字/秒折算降低字数（避免对话过载）。
    try:
        shot_target = int(settings_store.load_settings().get("shot_target_seconds", 10))
    except (TypeError, ValueError):
        shot_target = 10
    shot_target = max(pacing.MIN_SEC, min(pacing.MAX_SEC, shot_target))
    shot_chars = pacing.target_chars(shot_target)

    shots: list[dict] = list(existing_shots or [])
    blocks: list[dict] = []
    # cross-episode handoff: seed the first chunk with the previous episode's
    # closing handoff so the new episode picks up where the last one left off.
    prev_handoff = prev_handoff_init or "（首块，无上文）"
    counter = len(shots)

    for ci in range(start_chunk, total):
        chunk = chunks[ci]
        chunk_text = "\n".join(_segment_prompt_line(s) for s in chunk)
        prompt = tpl.render(body, {
            "story_bible": _bible_summary(bible, chunk_text, prev_handoff),
            "prev_handoff": prev_handoff,
            "chunk": chunk_text,
            "shot_target": shot_target,
            "shot_chars": shot_chars,
        })
        logger.info(f"[Analysis] Stage2 拆解块 {ci+1}/{total}（{len(chunk)} 段）")
        result = llm.chat_json(
            [{"role": "user", "content": prompt}],
            model=model,
            temperature=0.5,
        )
        chunk_shots = result if isinstance(result, list) else (
            result.get("shots", []) if isinstance(result, dict) else [])
        if not isinstance(chunk_shots, list) or not chunk_shots:
            raise llm.LLMError(f"拆解块 {ci + 1} 未返回有效分镜数组")
        # Valid source seqs for this chunk; used to repair bad/duplicate seq so the
        # worktable「原文」maps each shot to the source paragraph it derives from.
        chunk_seqs = [s.get("seq") for s in chunk if s.get("seq") is not None]
        seq_set = set(chunk_seqs)
        n_shots = len(chunk_shots)
        chunk_out: list[dict] = []
        for si, raw_sh in enumerate(chunk_shots):
            sh = _normalize_shot(raw_sh, chunk_seqs=chunk_seqs, shot_idx=si)
            # 原文映射：保存完整 src_seq/src_text。一个分镜可能由多个连续源段合并，
            # 不能只显示起始 seq 的单段原文，否则工作台「原文」会看起来被截断。
            start_seq, src_seq, src_text, src_matched, src_marker_no = _source_span_from_shot(
                sh, chunk, chunk_seqs, seq_set, si, n_shots)
            sh["seq"] = start_seq
            sh["src_seq"] = src_seq
            sh["src_text"] = src_text
            if src_marker_no is not None:
                sh["src_marker"] = f"分镜标记{src_marker_no}"
            sh["_src_content_match"] = src_matched
            # 按台词体量 + 情绪估算单镜时长（正常 5 字/秒，急/怒更快、悲/沉更慢），
            # 钳制到 [3,15] 秒（视频模型单条上限 15s）。LLM 已给时则尊重其值。
            if not sh.get("duration"):
                sh["duration"] = pacing.estimate_shot_seconds(
                    sh.get("dialogue", ""), sh.get("action", ""),
                    sh.get("emotion") or sh.get("mood") or "",
                    target_seconds=shot_target)
            chunk_out.append(sh)
            prev_handoff = sh.get("handoff") or prev_handoff
        _fill_missing_source_ranges(chunk_out, chunk, chunk_seqs)
        chunk_out = _merge_shots_for_pacing(chunk_out, shot_target)
        block_shot_nos = []
        for sh in chunk_out:
            _normalize_duration_for_target(sh, shot_target)
            counter += 1
            sh["shot_no"] = f"S{episode_no:02d}-C{counter:03d}"
            block_shot_nos.append(sh["shot_no"])
        shots.extend(chunk_out)
        blocks.append({"index": ci, "chunk_seq": [s.get("seq") for s in chunk], "shot_nos": block_shot_nos})
        if on_progress:
            on_progress(ci + 1, total)

    return {"shots": shots, "blocks": blocks, "chunk_total": total}


def run_decompose_manual(
    project: dict,
    manual_segments: list[str],
    *,
    model: Optional[str] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
    episode_no: int = 1,
    prev_handoff_init: Optional[str] = None,
) -> dict:
    """Stage 2 (手动分镜) -> {"shots": [...], "blocks": [...]}.

    The human has already decided every shot boundary (one entry of
    *manual_segments* == one shot). The LLM is still in charge of the rest:
    for each human segment it fills the structured fields (场景/角色/动作/机位/
    对白/接口/时长) via the ``manual_shot_structure`` template, carrying the
    previous shot's handoff forward for continuity. 强制 LLM — no fallback.
    """
    bible = project.get("story_bible") or {}
    segs = [s for s in (t.strip() for t in (manual_segments or [])) if s]
    total = len(segs)
    body = tpl.get_template_body("manual_shot_structure")
    try:
        shot_target = int(settings_store.load_settings().get("shot_target_seconds", 10))
    except (TypeError, ValueError):
        shot_target = 10
    shot_target = max(pacing.MIN_SEC, min(pacing.MAX_SEC, shot_target))

    shots: list[dict] = []
    block_shot_nos: list[str] = []
    prev_handoff = prev_handoff_init or "（首镜，无上文）"

    for i, text in enumerate(segs):
        prompt = tpl.render(body, {
            "story_bible": _bible_summary(bible, text, prev_handoff),
            "prev_handoff": prev_handoff,
            "shot_text": text,
            "shot_target": shot_target,
        })
        logger.info(f"[Analysis] Stage2 手动分镜·结构化 {i+1}/{total}")
        result = llm.chat_json(
            [{"role": "user", "content": prompt}], model=model, temperature=0.4)
        sh = result[0] if isinstance(result, list) and result else (
            result if isinstance(result, dict) else {})
        sh = _normalize_shot(sh, chunk_seqs=[i + 1], shot_idx=i)
        sh["shot_no"] = f"S{episode_no:02d}-C{i+1:03d}"
        sh["seq"] = i + 1
        # 人工划定的原文片段即本镜出处，直接落 src_text（保证原文与分镜一致）。
        sh["src_seq"] = [i + 1]
        sh["src_text"] = text
        if not sh.get("duration"):
            sh["duration"] = pacing.estimate_shot_seconds(
                sh.get("dialogue", ""), sh.get("action", ""),
                sh.get("emotion") or sh.get("mood") or "",
                target_seconds=shot_target)
        _normalize_duration_for_target(sh, shot_target)
        shots.append(sh)
        block_shot_nos.append(sh["shot_no"])
        prev_handoff = sh.get("handoff") or prev_handoff
        if on_progress:
            on_progress(i + 1, total)

    blocks = [{"index": 0, "chunk_seq": [s["seq"] for s in shots], "shot_nos": block_shot_nos}]
    return {"shots": shots, "blocks": blocks, "chunk_total": total}
