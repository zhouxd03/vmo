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
    bible.setdefault("title", "")
    bible.setdefault("logline", "")
    bible.setdefault("style", "")
    bible.setdefault("characters", [])
    bible.setdefault("scenes", [])
    bible.setdefault("props", [])
    bible.setdefault("continuity_constraints", [])
    return bible


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
) -> dict:
    """Stage 2 -> {"shots": [...], "blocks": [...]}.

    Resumable: start_chunk + existing_shots let a failed run continue.
    """
    bible = project.get("story_bible") or {}
    segments = project.get("segments", [])
    chunks = _chunk_segments(segments, chunk_chars)
    total = len(chunks)
    body = tpl.get_template_body("batch_decompose")
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
        chunk_shots = result if isinstance(result, list) else result.get("shots", [])
        # Valid source seqs for this chunk; used to repair bad/duplicate seq so the
        # worktable「原文」maps each shot to the source paragraph it derives from.
        chunk_seqs = [s.get("seq") for s in chunk if s.get("seq") is not None]
        seq_set = set(chunk_seqs)
        n_shots = len(chunk_shots)
        block_shot_nos = []
        for si, sh in enumerate(chunk_shots):
            counter += 1
            sh["shot_no"] = f"S{episode_no:02d}-C{counter:03d}"
            sh["seq"] = _resolve_src_seq(sh.get("seq"), seq_set, chunk_seqs, si, n_shots)
            # 按台词体量 + 情绪估算单镜时长（正常 5 字/秒，急/怒更快、悲/沉更慢），
            # 钳制到 [3,15] 秒（视频模型单条上限 15s）。LLM 已给时则尊重其值。
            if not sh.get("duration"):
                sh["duration"] = pacing.estimate_shot_seconds(
                    sh.get("dialogue", ""), sh.get("action", ""),
                    sh.get("emotion") or sh.get("mood") or "",
                    target_seconds=shot_target)
            shots.append(sh)
            block_shot_nos.append(sh["shot_no"])
            prev_handoff = sh.get("handoff") or prev_handoff
        blocks.append({"index": ci, "chunk_seq": [s.get("seq") for s in chunk], "shot_nos": block_shot_nos})
        if on_progress:
            on_progress(ci + 1, total)

    return {"shots": shots, "blocks": blocks, "chunk_total": total}
