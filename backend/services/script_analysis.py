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
    out["continuity_constraints"] = [
        str(x).strip() for x in _as_list(out.get("continuity_constraints")) if str(x).strip()
    ]
    if not (out["characters"] or out["scenes"] or out["props"] or out.get("style")):
        raise llm.LLMError("全局分析结果缺少人物/场景/道具/风格等有效信息，请换模型或调整原文后重试")
    return out


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
    matched_seqs, matched_text = _match_source_range(shot, chunk)
    if matched_seqs and matched_text:
        return matched_seqs[0], matched_seqs, matched_text, True

    start = _resolve_src_seq(shot.get("seq"), seq_set, chunk_seqs, shot_idx, n_shots)
    raw_end = shot.get("seq_end") or shot.get("end_seq") or shot.get("src_seq_end")
    end = _coerce_int(raw_end)
    if end is not None and end in seq_set:
        seqs = _contiguous_seq_range(chunk_seqs, start, end)
    else:
        seqs = [start] if start is not None else []
    return start, seqs, _src_text_for_seqs(chunk, seqs), False


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
        chunk_text = "\n".join(f"[{s.get('seq')}] {s.get('text','')}" for s in chunk)
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
        block_shot_nos = []
        chunk_out: list[dict] = []
        for si, raw_sh in enumerate(chunk_shots):
            sh = _normalize_shot(raw_sh, chunk_seqs=chunk_seqs, shot_idx=si)
            counter += 1
            sh["shot_no"] = f"S{episode_no:02d}-C{counter:03d}"
            # 原文映射：保存完整 src_seq/src_text。一个分镜可能由多个连续源段合并，
            # 不能只显示起始 seq 的单段原文，否则工作台「原文」会看起来被截断。
            start_seq, src_seq, src_text, src_matched = _source_span_from_shot(
                sh, chunk, chunk_seqs, seq_set, si, n_shots)
            sh["seq"] = start_seq
            sh["src_seq"] = src_seq
            sh["src_text"] = src_text
            sh["_src_content_match"] = src_matched
            # 按台词体量 + 情绪估算单镜时长（正常 5 字/秒，急/怒更快、悲/沉更慢），
            # 钳制到 [3,15] 秒（视频模型单条上限 15s）。LLM 已给时则尊重其值。
            if not sh.get("duration"):
                sh["duration"] = pacing.estimate_shot_seconds(
                    sh.get("dialogue", ""), sh.get("action", ""),
                    sh.get("emotion") or sh.get("mood") or "",
                    target_seconds=shot_target)
            chunk_out.append(sh)
            block_shot_nos.append(sh["shot_no"])
            prev_handoff = sh.get("handoff") or prev_handoff
        _fill_missing_source_ranges(chunk_out, chunk, chunk_seqs)
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
        shots.append(sh)
        block_shot_nos.append(sh["shot_no"])
        prev_handoff = sh.get("handoff") or prev_handoff
        if on_progress:
            on_progress(i + 1, total)

    blocks = [{"index": 0, "chunk_seq": [s["seq"] for s in shots], "shot_nos": block_shot_nos}]
    return {"shots": shots, "blocks": blocks, "chunk_total": total}
