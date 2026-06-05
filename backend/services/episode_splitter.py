"""Auto-split an imported script into episodes (集).

Product rules:
- If the script already declares episodes (第X集 / 第X章 / 第X回 / 第X话 / 第X幕 /
  EP n / Episode n / Chapter n markers), split on those boundaries and keep the
  declared titles verbatim.
- Otherwise auto-split by volume: target ~TARGET_CHARS chars per episode
  (≈3 分钟/集，按 120字/15秒 ≈ 8字/秒 折算 ≈ 1440 字), snapping to whole segment
  boundaries (paragraphs / shot blocks) so sentences are never cut mid-way.

Returns a list of episode payloads:
    {"name", "source_type", "raw_text", "parsed"}
where parsed = {"source_type", "segments": [{seq, text, time?}], "char_count"}
with seq re-numbered 1..N within each episode.
"""

import logging
import re
from typing import Optional

from . import script_parser

logger = logging.getLogger("batch_studio")

# LLM 分集摘要规模上限：超过则不送 LLM（改用体量切分），避免上下文过长。
_LLM_MAX_SEGMENTS = 600
_LLM_DIGEST_HEAD = 36

# 3 分钟/集，按 120 字 / 15 秒 折算 ≈ 1440 字。
TARGET_CHARS = 1440
# 末集若小于该阈值则并回上一集，避免出现极短的尾集。
MIN_TAIL_CHARS = 480

# 集/章标记：第<数字>集|章|回|话|幕，或 EP/Episode/Chapter <数字>。
_EP_HEADER_RE = re.compile(
    r"^\s*(?:"
    r"第\s*[0-9〇零一二三四五六七八九十百千两]+\s*(?:集|章|回|话|幕)"
    r"|(?:ep|episode|chapter)\s*\.?\s*\d+"
    r")",
    re.IGNORECASE,
)
# 集标题行一般较短（"第一集 弃子"）；过长的行更可能是把"第三回合…"写进正文的句子，
# 不当作分集标记，避免误切。
_EP_HEADER_MAX_LEN = 30


def _first_line(text: str) -> str:
    for ln in (text or "").splitlines():
        s = ln.strip()
        if s:
            return s
    return ""


def _is_ep_header_line(line: str) -> bool:
    """行级集标记判定：整行以第X集/章/回… 开头且足够短，才算分集标题。"""
    s = (line or "").strip()
    if not s or len(s) > _EP_HEADER_MAX_LEN:
        return False
    return bool(_EP_HEADER_RE.match(s))


def _is_ep_header(seg_text: str) -> bool:
    return _is_ep_header_line(_first_line(seg_text))


def _split_text_by_headers(text: str) -> Optional[list[tuple[str, str]]]:
    """直接在原始文本上按行扫描分集标记，返回 [(标题, 正文)]。

    不依赖解析器的分段结果——解析器可能把整篇并成一段而漏掉标记。识别到
    >= 2 个标记才返回；标题行之前的引子并入第一集。少于 2 个返回 None。
    """
    lines = (text or "").split("\n")
    hits: list[tuple[int, str]] = [
        (i, ln.strip()) for i, ln in enumerate(lines) if _is_ep_header_line(ln)
    ]
    if len(hits) < 2:
        return None
    chunks: list[tuple[str, str]] = []
    for k, (idx, title) in enumerate(hits):
        end = hits[k + 1][0] if k + 1 < len(hits) else len(lines)
        body = "\n".join(lines[idx + 1:end]).strip()
        chunks.append((title, body))
    preface = "\n".join(lines[:hits[0][0]]).strip()
    if preface:
        t0, b0 = chunks[0]
        chunks[0] = (t0, (preface + "\n\n" + b0).strip() if b0 else preface)
    return chunks


def _split_by_markers(segments: list[dict]) -> list[tuple[Optional[str], list[dict]]]:
    groups: list[tuple[Optional[str], list[dict]]] = []
    cur_name: Optional[str] = None
    cur: list[dict] = []
    for seg in segments:
        if _is_ep_header(seg.get("text", "")):
            if cur:
                groups.append((cur_name, cur))
            cur_name = _first_line(seg.get("text", ""))
            cur = [seg]
        else:
            cur.append(seg)
    if cur:
        groups.append((cur_name, cur))
    return groups


def _split_by_volume(segments: list[dict], target: int) -> list[tuple[Optional[str], list[dict]]]:
    groups: list[list[dict]] = []
    cur: list[dict] = []
    size = 0
    for seg in segments:
        cur.append(seg)
        size += len(seg.get("text", ""))
        if size >= target:
            groups.append(cur)
            cur = []
            size = 0
    if cur:
        tail = sum(len(s.get("text", "")) for s in cur)
        if groups and tail < MIN_TAIL_CHARS:
            groups[-1].extend(cur)  # fold a runt tail back into the last episode
        else:
            groups.append(cur)
    return [(None, g) for g in groups]


def _seg_digest(segments: list[dict]) -> str:
    """一行一段：「[seq] 段首文字」，供 LLM 自适应判断分集边界。"""
    lines = []
    for s in segments:
        t = (s.get("text", "") or "").strip().replace("\n", " ")
        lines.append(f"[{s.get('seq')}] {t[:_LLM_DIGEST_HEAD]}")
    return "\n".join(lines)


def _split_by_llm(
    segments: list[dict], model: Optional[str] = None,
) -> Optional[list[tuple[Optional[str], list[dict]]]]:
    """LLM 自适应分集兜底：启发式标记识别不到时，送段落摘要让 LLM 找分集起点。
    返回 groups；失败/不足 2 集时返回 None（交由体量切分兜底）。"""
    if len(segments) < 2 or len(segments) > _LLM_MAX_SEGMENTS:
        return None
    # late import：避免模块在无 LLM 凭据的纯解析场景被强行拉起依赖
    from ..core import prompt_templates as tpl
    from . import llm

    digest = _seg_digest(segments)
    if not digest.strip():
        return None
    prompt = tpl.render(tpl.get_template_body("episode_split"), {"digest": digest})
    try:
        data = llm.chat_json([{"role": "user", "content": prompt}], model=model)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[EpisodeSplit] LLM 分集失败，回退体量切分: {e}")
        return None

    eps = data.get("episodes") if isinstance(data, dict) else (
        data if isinstance(data, list) else None)
    if not eps or len(eps) < 2:
        return None

    seq_to_idx = {s.get("seq"): i for i, s in enumerate(segments)}
    starts: list[tuple[int, Optional[str]]] = []
    for e in eps:
        if not isinstance(e, dict):
            continue
        idx = seq_to_idx.get(e.get("start_seq"))
        if idx is not None:
            starts.append((idx, (e.get("name") or "").strip() or None))
    # de-dupe by index, keep first name, sort
    by_idx: dict[int, Optional[str]] = {}
    for idx, name in starts:
        by_idx.setdefault(idx, name)
    ordered = sorted(by_idx.items())
    if not ordered:
        return None
    if ordered[0][0] != 0:
        ordered.insert(0, (0, None))

    groups: list[tuple[Optional[str], list[dict]]] = []
    for k, (idx, name) in enumerate(ordered):
        end = ordered[k + 1][0] if k + 1 < len(ordered) else len(segments)
        if idx < end:
            groups.append((name, segments[idx:end]))
    if len(groups) < 2:
        return None
    total_chars = sum(len(s.get("text", "")) for s in segments)
    avg_chars = total_chars / max(1, len(groups))
    # Guard against malformed LLM output that turns nearly every paragraph into
    # an episode. Volume fallback is safer than creating dozens of runt episodes.
    if len(groups) > max(3, len(segments) // 2) and avg_chars < MIN_TAIL_CHARS:
        logger.warning("[EpisodeSplit] LLM 分集过碎，回退体量切分: groups=%s avg_chars=%.1f",
                       len(groups), avg_chars)
        return None
    return groups


def _build_episode(name: str, segs: list[dict], source_type: str) -> dict:
    ep_segs = []
    for j, s in enumerate(segs, start=1):
        ns = {"seq": j, "text": s.get("text", "")}
        if s.get("time"):
            ns["time"] = s["time"]
        ep_segs.append(ns)
    raw = "\n\n".join(s.get("text", "") for s in segs)
    char_count = sum(len(s.get("text", "")) for s in segs)
    return {
        "name": name,
        "source_type": source_type,
        "raw_text": raw,
        "parsed": {
            "source_type": source_type,
            "segments": ep_segs,
            "char_count": char_count,
        },
    }


def split_into_episodes(
    text: str,
    file_type: str,
    *,
    target_chars: int = TARGET_CHARS,
    use_llm: bool = True,
    llm_model: Optional[str] = None,
    meta: Optional[dict] = None,
) -> list[dict]:
    """Parse + auto-split into episode payloads (see module docstring).

    分集优先级：① 启发式标记（第X集/章/回…）；② 识别不到时 LLM 自适应切分
    （use_llm=True）；③ 仍不足 2 集则按体量切分兜底。``meta`` 若给出，会写入
    ``{"method": "markers|llm|volume", "episodes": N}`` 供前端提示。"""
    # ① 原始文本行级扫描分集标记（最可靠，不受解析器分段影响）。命中则每集
    #    单独解析为分镜片段。
    header_chunks = _split_text_by_headers(text)
    if header_chunks:
        episodes: list[dict] = []
        for i, (title, body) in enumerate(header_chunks, start=1):
            p = script_parser.parse_script(body, file_type)
            segs = p.get("segments", [])
            stype = p.get("source_type", "txt")
            episodes.append(_build_episode(title or f"第{i}集", segs, stype))
        if meta is not None:
            meta["method"] = "markers"
            meta["episodes"] = len(episodes)
        return episodes

    parsed = script_parser.parse_script(text, file_type)
    segments = parsed.get("segments", [])
    source_type = parsed.get("source_type", "txt")
    if not segments:
        return []

    # ② 段级标记兜底；③ LLM 自适应；④ 体量切分。
    marker_count = sum(1 for s in segments if _is_ep_header(s.get("text", "")))
    method = "volume"
    if marker_count >= 2:
        groups = _split_by_markers(segments)
        method = "markers"
    else:
        groups = _split_by_llm(segments, model=llm_model) if use_llm else None
        if groups:
            method = "llm"
        else:
            groups = _split_by_volume(segments, target_chars)
            method = "volume"

    episodes = []
    for i, (name, segs) in enumerate(groups, start=1):
        ep_segs = []
        for j, s in enumerate(segs, start=1):
            ns = {"seq": j, "text": s.get("text", "")}
            if s.get("time"):
                ns["time"] = s["time"]
            ep_segs.append(ns)
        raw = "\n\n".join(s.get("text", "") for s in segs)
        char_count = sum(len(s.get("text", "")) for s in segs)
        episodes.append({
            "name": name or f"第{i}集",
            "source_type": source_type,
            "raw_text": raw,
            "parsed": {
                "source_type": source_type,
                "segments": ep_segs,
                "char_count": char_count,
            },
        })
    if meta is not None:
        meta["method"] = method
        meta["episodes"] = len(episodes)
    return episodes
