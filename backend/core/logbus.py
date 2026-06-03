"""In-memory log buffer surfaced to the frontend (incremental polling)."""

import logging
from collections import deque

_buffer: deque = deque(maxlen=500)
_counter = 0

_FILTER = ["/api/logs", "since_id="]


class MemoryLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        global _counter
        msg = record.getMessage()
        if any(kw in msg for kw in _FILTER):
            return
        _counter += 1
        _buffer.append({"id": _counter, "level": record.levelname, "msg": msg})


def get_logs(since_id: int = 0) -> list:
    return [e for e in _buffer if e["id"] > since_id]


def setup_logging() -> logging.Logger:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    handler = MemoryLogHandler()
    handler.setLevel(logging.DEBUG)
    logging.getLogger().addHandler(handler)
    logger = logging.getLogger("batch_studio")
    logger.setLevel(logging.INFO)
    return logger
