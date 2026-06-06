"""SRT / TXT script parsing into ordered segments.

Logic aligned with the verified xingyao parser (modules/script_parser/parser.py)
but returns structured segments carrying a sequence number, which becomes the
basis for downstream shot numbering (S01-C012 etc.). SRT keeps its own subtitle
index; TXT segments are numbered 1..N in order.
"""

import re
from typing import Any

_PLACEHOLDER_PREFIX_RE = re.compile(
    r"^(?:"
    r"\d+\s*[.、)\]】）]\s*"
    r"|第\s*\d+\s*(?:集|章|段|镜|话|幕)?\s*[:：.、)\]】）]?\s*"
    r"|(?:镜号|分镜头|分镜|镜头|shot|scene)\s*[:：]?\s*\d*\s*[:：.、)\]】）]?\s*"
    r")",
    re.IGNORECASE,
)
_PLACEHOLDER_VALUES = {"待补充", "略", "无", "空", "暂无", "tbd", "n/a", "na", "-", "—"}

_STRUCTURED_SHOT_HEADER_RE = re.compile(
    r"^(?:[#>*\-\s]+)?(?:\*\*|__)?\s*(?:分镜头|分镜|镜头|shot|scene)\s*[:：#\.、\-]*\s*\d+\b",
    re.IGNORECASE,
)
_SHOT_PREFIX_RE = re.compile(r"^(?:镜头|分镜|画面|场景|旁白|字幕|第\d+|\d+[.、)\]】）])")
# Fixed explicit separators override paragraph blank lines when the source
# text has its own empty-line formatting. Numbered markers make source mapping
# deterministic after decomposition:
#   - <<<分镜标记>>> / <<<分镜标记1>>> / <<<分镜标记 1>>>
#   - <<<分集标记>>> / <<<分集标记1>>> / <<<分集标记 1>>>
_EXPLICIT_MARKER_LINE_RE = re.compile(
    r"^\s*<<<\s*(分镜标记|分集标记)\s*(\d+)?\s*>>>\s*$"
)
_EXPLICIT_SPLIT_RE = re.compile(
    r"(?:^|\n)\s*<<<\s*(?:分镜标记|分集标记)\s*\d*\s*>>>\s*(?:\n|$)"
)


def _strip_markup(line: str) -> str:
    return re.sub(r"^(?:[#>*\-\s]+|\*\*|__)+", "", line).strip()


def _is_placeholder(value: str) -> bool:
    if not value or not value.strip():
        return True
    s = _strip_markup(value)
    for _ in range(4):
        ns = _PLACEHOLDER_PREFIX_RE.sub("", s, count=1).strip()
        if ns == s:
            break
        s = ns
    if not s:
        return True
    return s.casefold() in _PLACEHOLDER_VALUES


def _normalize(text: str) -> str:
    t = text.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    # 行尾空白清理：使「只含空格/制表符/全角空格/不换行空格(\xa0)」的空行成为真正
    # 的空行分隔，否则 "\n\xa0\n" 不会被 "\n\n" 段落切分识别（剧本常见此问题）。
    # [^\S\n] = 除换行外的任意空白（含 \xa0、\u3000、\t 等）。
    t = re.sub(r"[^\S\n]+\n", "\n", t)
    return t


# ── TXT ──────────────────────────────────────────────────────────────────
def _split_structured(text: str) -> list[str]:
    lines = text.split("\n")
    blocks: list[str] = []
    buf: list[str] = []
    for raw in lines:
        line = raw.rstrip()
        if _STRUCTURED_SHOT_HEADER_RE.match(_strip_markup(line)):
            if buf and any(x.strip() for x in buf):
                blocks.append("\n".join(buf).strip())
            buf = [line.strip()]
            continue
        if buf:
            buf.append(line)
    if buf and any(x.strip() for x in buf):
        blocks.append("\n".join(buf).strip())
    return [b for b in blocks if b]


def _split_by_explicit_markers(text: str) -> list[str]:
    """Split by explicit separators before falling back to blank lines.

    This makes shot boundaries stable even when the source text itself contains
    blank lines for prose formatting or dialogue spacing.
    """
    parts = _EXPLICIT_SPLIT_RE.split(text or "")
    return [p.strip() for p in parts if p and p.strip()]


def _split_explicit_marker_segments(text: str) -> list[dict[str, Any]]:
    """Return blocks cut by explicit markers, preserving marker metadata."""
    blocks: list[dict[str, Any]] = []
    buf: list[str] = []
    current_type: str | None = None
    current_no: int | None = None
    current_episode: int | None = None

    def flush() -> None:
        nonlocal buf
        block_text = "\n".join(buf).strip()
        if not block_text:
            buf = []
            return
        blocks.append({
            "text": block_text,
            "marker_type": current_type,
            "marker_no": current_no,
            "episode_marker": current_episode,
        })
        buf = []

    for raw in (text or "").splitlines():
        m = _EXPLICIT_MARKER_LINE_RE.match(raw)
        if m:
            flush()
            label, no_raw = m.groups()
            no = int(no_raw) if no_raw else None
            current_type = "shot" if label == "分镜标记" else "episode"
            current_no = no
            if current_type == "episode":
                current_episode = no
            continue
        buf.append(raw.rstrip())
    flush()
    return [b for b in blocks if b.get("text")]


def _parse_txt(text: str) -> list[str]:
    norm = _normalize(text)
    structured = _split_structured(norm)
    if len(structured) > 1:
        return structured
    explicit = _split_by_explicit_markers(norm)
    if explicit and has_explicit_markers(norm):
        return explicit
    blocks = [b.strip() for b in norm.split("\n\n") if b.strip()]
    if len(blocks) > 1:
        return blocks
    lines = [ln.strip() for ln in norm.split("\n") if ln.strip()]
    if not lines:
        return []
    merged: list[str] = []
    buf: list[str] = []
    for line in lines:
        if _SHOT_PREFIX_RE.match(line) and buf:
            merged.append(" ".join(buf).strip())
            buf = [line]
            continue
        buf.append(line)
    if buf:
        merged.append(" ".join(buf).strip())
    return [m for m in merged if m]


def _parse_txt_segments(text: str) -> list[dict[str, Any]]:
    norm = _normalize(text)
    explicit = _split_explicit_marker_segments(norm)
    if explicit and has_explicit_markers(norm):
        segments: list[dict[str, Any]] = []
        for i, block in enumerate(explicit):
            if _is_placeholder(block.get("text", "")):
                continue
            seg = {"seq": len(segments) + 1, "text": block["text"]}
            for key in ("marker_type", "marker_no", "episode_marker"):
                if block.get(key) is not None:
                    seg[key] = block[key]
            segments.append(seg)
        return segments
    raw = [b for b in _parse_txt(text) if not _is_placeholder(b)]
    return [{"seq": i + 1, "text": b} for i, b in enumerate(raw)]


def has_explicit_markers(text: str) -> bool:
    return bool(_EXPLICIT_SPLIT_RE.search(_normalize(text or "")))


# ── SRT ──────────────────────────────────────────────────────────────────
def _parse_srt(text: str) -> list[dict]:
    """Returns list of {index, time, text} preserving subtitle numbering."""
    norm = _normalize(text)
    lines = norm.splitlines()
    results: list[dict] = []
    i = 0
    while i < len(lines):
        if not lines[i].strip().isdigit():
            i += 1
            continue
        seq = int(lines[i].strip())
        i += 1
        time = ""
        if i < len(lines) and "-->" in lines[i]:
            time = lines[i].strip()
            i += 1
        text_lines: list[str] = []
        while i < len(lines) and lines[i].strip():
            text_lines.append(lines[i].strip())
            i += 1
        combined = " ".join(text_lines).strip()
        if combined:
            results.append({"index": seq, "time": time, "text": combined})
        i += 1
    return results


def parse_script(text: str, file_type: str) -> dict[str, Any]:
    """Parse raw text into ordered segments.

    Returns: {"source_type", "segments": [{seq, text, time?}], "char_count"}
    """
    file_type = (file_type or "").lower()
    char_count = len(text or "")
    if file_type == "srt":
        srt = _parse_srt(text)
        segments = [
            {"seq": s["index"], "text": s["text"], "time": s["time"]}
            for s in srt
            if not _is_placeholder(s["text"])
        ]
        if not segments:  # fallback: treat as txt
            file_type = "txt"
    if file_type != "srt":
        segments = _parse_txt_segments(text)
        source_type = "txt"
    else:
        source_type = "srt"
    return {"source_type": source_type, "segments": segments, "char_count": char_count}
