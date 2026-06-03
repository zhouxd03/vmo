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
from . import llm

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
    if add.get("summary") and not base.get("summary"):
        base["summary"] = add["summary"]
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


def _bible_summary(bible: dict) -> str:
    """Compact StoryBible for stage-2 context (keep tokens small)."""
    def names(items, key="name"):
        return "、".join(i.get(key, "") for i in items if i.get(key))
    parts = [
        f"风格: {bible.get('style','')}",
        f"人物: {names(bible.get('characters', []))}",
        f"场景: {names(bible.get('scenes', []))}",
        f"道具: {names(bible.get('props', []))}",
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
    bible_sum = _bible_summary(bible)

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
            "story_bible": bible_sum,
            "prev_handoff": prev_handoff,
            "chunk": chunk_text,
        })
        logger.info(f"[Analysis] Stage2 拆解块 {ci+1}/{total}（{len(chunk)} 段）")
        result = llm.chat_json(
            [{"role": "user", "content": prompt}],
            model=model,
            temperature=0.5,
        )
        chunk_shots = result if isinstance(result, list) else result.get("shots", [])
        block_shot_nos = []
        for sh in chunk_shots:
            counter += 1
            sh["shot_no"] = f"S{episode_no:02d}-C{counter:03d}"
            sh.setdefault("seq", chunk[0].get("seq"))
            shots.append(sh)
            block_shot_nos.append(sh["shot_no"])
            prev_handoff = sh.get("handoff") or prev_handoff
        blocks.append({"index": ci, "chunk_seq": [s.get("seq") for s in chunk], "shot_nos": block_shot_nos})
        if on_progress:
            on_progress(ci + 1, total)

    return {"shots": shots, "blocks": blocks, "chunk_total": total}
