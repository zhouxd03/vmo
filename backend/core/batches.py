"""Batch persistence (Phase 4).

A batch is a queue of generation tasks (image or video) for one project. Each
task maps to a shot (or a manual prompt) and tracks its own status/attempts so
the engine can retry failures and resume from where it stopped.

Stored under data/projects/<pid>/batches/<bid>.json with a per-project index.
"""

import threading
import time
import uuid
from typing import Any, Optional

from .paths import PROJECTS_DIR
from .store import read_json, write_json

# Guards read-modify-write of a batch file so concurrent engine threads
# (within one process) don't clobber each other's task updates.
_mutate_lock = threading.RLock()


def _batch_dir(pid: str):
    d = PROJECTS_DIR / pid / "batches"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _batch_path(pid: str, bid: str):
    return _batch_dir(pid) / f"{bid}.json"


def _index_path(pid: str):
    return _batch_dir(pid) / "index.json"


def _load_index(pid: str) -> list:
    return read_json(_index_path(pid), [])


def _save_index(pid: str, idx: list) -> None:
    write_json(_index_path(pid), idx)


def _summarize(b: dict) -> dict:
    tasks = b.get("tasks", [])
    done = sum(1 for t in tasks if t["status"] == "done")
    err = sum(1 for t in tasks if t["status"] == "error")
    return {
        "id": b["id"],
        "name": b.get("name", ""),
        "kind": b.get("kind", "image"),
        "status": b.get("status", "pending"),
        "total": len(tasks),
        "done": done,
        "error": err,
        "concurrency": b.get("concurrency", 2),
        # surface episode membership in the lightweight index so the worktable /
        # episode bar can group batches per-episode without fetching every detail
        "episode_id": (b.get("params") or {}).get("episode_id"),
        "created_at": b.get("created_at"),
        "updated_at": b.get("updated_at"),
    }


def list_batches(pid: str) -> list:
    return sorted(_load_index(pid), key=lambda x: x.get("created_at", 0), reverse=True)


def get_batch(pid: str, bid: str) -> Optional[dict]:
    return read_json(_batch_path(pid, bid), None)


def create_batch(pid: str, kind: str, name: str, tasks: list,
                 concurrency: int = 2, params: Optional[dict] = None,
                 max_attempts: int = 3) -> dict:
    if kind not in ("image", "video"):
        raise ValueError(f"未知批次类型: {kind}")
    if not tasks:
        raise ValueError("批次任务为空")
    bid = uuid.uuid4().hex[:12]
    now = time.time()
    norm_tasks = []
    for i, t in enumerate(tasks):
        norm_tasks.append({
            "id": uuid.uuid4().hex[:8],
            "index": i,
            "shot_no": t.get("shot_no", ""),
            "seq": t.get("seq"),
            "prompt": t.get("prompt", ""),
            "materials": t.get("materials", []),
            "params": t.get("params", {}),
            # continuity fields (Phase 5) — preserved for the continuity engine
            "scene": t.get("scene", ""),
            "characters": t.get("characters", []),
            "props": t.get("props", []),
            "action": t.get("action", ""),
            "camera": t.get("camera", ""),
            "handoff": t.get("handoff", ""),
            "status": "pending",
            "attempts": 0,
            "max_attempts": max_attempts,
            "result": None,
            "error": None,
            "updated_at": now,
        })
    batch = {
        "id": bid,
        "pid": pid,
        "kind": kind,
        "name": name or f"批次 {time.strftime('%m-%d %H:%M')}",
        "concurrency": max(1, int(concurrency or 1)),
        "params": params or {},
        "status": "pending",
        "tasks": norm_tasks,
        "created_at": now,
        "updated_at": now,
    }
    _save(pid, batch)
    idx = _load_index(pid)
    idx.append(_summarize(batch))
    _save_index(pid, idx)
    return batch


def _save(pid: str, batch: dict) -> None:
    batch["updated_at"] = time.time()
    write_json(_batch_path(pid, batch["id"]), batch)


def save_batch(pid: str, batch: dict) -> dict:
    _save(pid, batch)
    idx = [_summarize(batch) if b["id"] == batch["id"] else b for b in _load_index(pid)]
    _save_index(pid, idx)
    return batch


def update_task(pid: str, bid: str, task_id: str, patch: dict[str, Any]) -> Optional[dict]:
    with _mutate_lock:
        batch = get_batch(pid, bid)
        if not batch:
            return None
        for t in batch["tasks"]:
            if t["id"] == task_id:
                t.update(patch)
                t["updated_at"] = time.time()
                break
        return save_batch(pid, batch)


def set_status(pid: str, bid: str, status: str) -> Optional[dict]:
    with _mutate_lock:
        batch = get_batch(pid, bid)
        if not batch:
            return None
        batch["status"] = status
        return save_batch(pid, batch)


def reset_failed(pid: str, bid: str) -> Optional[dict]:
    with _mutate_lock:
        batch = get_batch(pid, bid)
        if not batch:
            return None
        for t in batch["tasks"]:
            if t["status"] == "error":
                t["status"] = "pending"
                t["error"] = None
                t["attempts"] = 0
        return save_batch(pid, batch)


def delete_batch(pid: str, bid: str) -> None:
    idx = [b for b in _load_index(pid) if b["id"] != bid]
    _save_index(pid, idx)
    p = _batch_path(pid, bid)
    if p.exists():
        p.unlink()
