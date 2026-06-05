"""Thread-safe JSON file persistence helpers."""

import json
import threading
import time
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
        tmp = path.with_name(f"{path.name}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        last_error = None
        for _ in range(5):
            try:
                tmp.replace(path)
                return
            except PermissionError as e:
                last_error = e
                time.sleep(0.08)
        try:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            return
        except PermissionError as e:
            last_error = e
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except PermissionError:
                pass
        if last_error:
            raise last_error
