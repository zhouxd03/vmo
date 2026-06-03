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

import re
from typing import Optional

from . import script_parser

# 3 分钟/集，按 120 字 / 15 秒 折算 ≈ 1440 字。
TARGET_CHARS = 1440
# 末集若小于该阈值则并回上一集，避免出现极短的尾集。
MIN_TAIL_CHARS = 480

# 集/章标记：第<数字>集|章|回|话|幕，或 EP/Episode/Chapter <数字>。
_EP_HEADER_RE = re.compile(
    r"^\s*(?:"
    r"第\s*[0-9〇零一二三四五六七八九十百千两]+\s*(?:集|章|回|话|幕)"
    r"|(?:ep|episode|chapter)\s*\.?\s*\d+"
    r")\b",
    re.IGNORECASE,
)


def _first_line(text: str) -> str:
    for ln in (text or "").splitlines():
        s = ln.strip()
        if s:
            return s
    return ""


def _is_ep_header(seg_text: str) -> bool:
    return bool(_EP_HEADER_RE.match(_first_line(seg_text)))


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


def split_into_episodes(
    text: str,
    file_type: str,
    *,
    target_chars: int = TARGET_CHARS,
) -> list[dict]:
    """Parse + auto-split into episode payloads (see module docstring)."""
    parsed = script_parser.parse_script(text, file_type)
    segments = parsed.get("segments", [])
    source_type = parsed.get("source_type", "txt")
    if not segments:
        return []

    marker_count = sum(1 for s in segments if _is_ep_header(s.get("text", "")))
    if marker_count >= 2:
        groups = _split_by_markers(segments)
    else:
        groups = _split_by_volume(segments, target_chars)

    episodes: list[dict] = []
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
    return episodes
