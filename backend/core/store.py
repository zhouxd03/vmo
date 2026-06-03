"""Thread-safe JSON file persistence helpers."""

import json
import threading
from pathlib import Path
from typing import Any

_lock = threading.Lock()


def read_json(path: Path, default: Any) -> Any:
    with _lock:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return default
    return default


def write_json(path: Path, data: Any) -> None:
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
