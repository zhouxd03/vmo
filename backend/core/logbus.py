"""In-memory log buffer surfaced to the frontend (incremental polling + export).

The buffer feeds the「日志调试」tab: the frontend polls :func:`get_logs`
incrementally to show live work-progress/status, and can download the full
buffer via :func:`export_text` to hand to a developer when reporting a bug.
"""

import logging
import re
import time
from collections import deque

_buffer: deque = deque(maxlen=2000)
_counter = 0

# Drop low-signal noise so the「日志调试」view stays readable: its own polling,
# static-asset/SPA fetches, the health probe, and favicon. Meaningful API calls
# (decompose/analyze/generate/projects…) and app logs are kept.
_FILTER = [
    "/api/logs", "since_id=", "/assets/", "/assets_static",
    "/favicon", "/api/health", "GET / HTTP",
]
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class MemoryLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        global _counter
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 — never let logging crash the app
            msg = str(record.msg)
        msg = _ANSI_RE.sub("", msg).strip()
        if not msg or any(kw in msg for kw in _FILTER):
            return
        _counter += 1
        _buffer.append({
            "id": _counter,
            "ts": record.created,           # epoch seconds (float)
            "level": record.levelname,
            "name": record.name,
            "msg": msg,
        })


def get_logs(since_id: int = 0) -> list:
    return [e for e in _buffer if e["id"] > since_id]


def clear_logs() -> int:
    """Drop all buffered entries. Returns how many were removed."""
    n = len(_buffer)
    _buffer.clear()
    return n


def _fmt_ts(ts: float) -> str:
    lt = time.localtime(ts)
    return time.strftime("%Y-%m-%d %H:%M:%S", lt) + f".{int((ts % 1) * 1000):03d}"


def export_text(app_version: str = "") -> str:
    """Render the whole buffer as a plain-text log document for download."""
    head = [
        "# Batch Studio 日志导出 (Log Export)",
        f"# 导出时间 Exported: {_fmt_ts(time.time())}",
        f"# 版本 Version: {app_version or 'unknown'}",
        f"# 条目数 Entries: {len(_buffer)} (buffer max {_buffer.maxlen})",
        "# " + "-" * 60,
        "",
    ]
    lines = [
        f"{_fmt_ts(e['ts'])} [{e['level']:<7}] {e['name']}: {e['msg']}"
        for e in _buffer
    ]
    return "\n".join(head + lines) + "\n"


def setup_logging() -> logging.Logger:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    handler = MemoryLogHandler()
    handler.setLevel(logging.DEBUG)
    logging.getLogger().addHandler(handler)
    logger = logging.getLogger("batch_studio")
    logger.setLevel(logging.INFO)
    return logger
