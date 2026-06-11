"""Batch persistence (Phase 4).

A batch is a queue of generation tasks for one project. Each task maps to a
shot or manual prompt and tracks its own status/attempts so the engine can
retry failures and resume from where it stopped.

Stored under data/projects/<pid>/batches/<bid>.json with a per-project index.
"""

import re
import threading
import time
import uuid
from typing import Any, Optional

from .paths import PROJECTS_DIR
from .store import read_json, write_json

# Guards read-modify-write of a batch file so concurrent engine threads within
# one process do not clobber each other's task updates.
_mutate_lock = threading.RLock()


def _batch_dir(pid: str):
    d = PROJECTS_DIR / pid / "batches"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _batch_path(pid: str, bid: str):
    return _batch_dir(pid) / f"{bid}.json"


def _safe_id_part(text: str, fallback: str = "batch") -> str:
    text = (text or "").strip()
    text = re.sub(r"[^A-Za-z0-9_-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_-")
    return (text or fallback)[:40]


def _index_path(pid: str):
    return _batch_dir(pid) / "index.json"


def _load_index(pid: str) -> list:
    return read_json(_index_path(pid), [])


def _save_index(pid: str, idx: list) -> None:
    write_json(_index_path(pid), idx)


def _summarize(b: dict) -> dict:
    tasks = b.get("tasks", [])
    done = sum(1 for t in tasks if t["status"] == "done")
    err = sum(1 for t in tasks if t["status"] == "error" and not t.get("error_dismissed_at"))
    skipped = sum(1 for t in tasks if t["status"] == "skipped")
    return {
        "id": b["id"],
        "name": b.get("name", ""),
        "kind": b.get("kind", "image"),
        "status": b.get("status", "pending"),
        "total": len(tasks),
        "done": done,
        "error": err,
        "skipped": skipped,
        "concurrency": b.get("concurrency", 2),
        "pause_requested": bool(b.get("pause_requested")),
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

    # Video calls are expensive and prompt-sensitive. A failed provider task may
    # already have consumed quota, so never create video tasks with automatic
    # resubmit attempts; users should inspect the error and manually retry.
    if kind == "video":
        max_attempts = 1

    now = time.time()
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(now))
    params = params or {}
    episode_label = params.get("episode_id") or ""
    first_shot = (tasks[0] or {}).get("shot_no") if tasks else ""
    label = "_".join(x for x in (kind, episode_label, first_shot) if x)
    bid = f"{_safe_id_part(label, kind)}_{stamp}_{uuid.uuid4().hex[:6]}"

    norm_tasks = []
    for i, t in enumerate(tasks):
        norm_tasks.append({
            "id": uuid.uuid4().hex[:8],
            "index": i,
            "shot_no": t.get("shot_no", ""),
            "seq": t.get("seq"),
            "src_text": t.get("src_text", ""),
            "prompt": t.get("prompt", ""),
            "materials": t.get("materials", []),
            "params": t.get("params", {}),
            # Continuity fields are preserved for the continuity engine.
            "scene": t.get("scene", ""),
            "scene_ref": t.get("scene_ref", ""),
            "characters": t.get("characters", []),
            "characters_ref": t.get("characters_ref", []),
            "props": t.get("props", []),
            "action": t.get("action", ""),
            "action_ref": t.get("action_ref", ""),
            "camera": t.get("camera", ""),
            "handoff": t.get("handoff", ""),
            "handoff_ref": t.get("handoff_ref", ""),
            "dialogue": t.get("dialogue", ""),
            "dialogue_ref": t.get("dialogue_ref", ""),
            "emotion": t.get("emotion", ""),
            "duration": t.get("duration"),
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
        "params": params,
        "status": "pending",
        "pause_requested": False,
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


def request_pause(pid: str, bid: str) -> Optional[dict]:
    with _mutate_lock:
        batch = get_batch(pid, bid)
        if not batch:
            return None
        batch["pause_requested"] = True
        for t in batch["tasks"]:
            if t.get("status") == "pending":
                t["status"] = "paused"
                t["stage"] = "paused"
                t["stage_label"] = "已暂停等待"
                t["updated_at"] = time.time()
            elif t.get("status") == "running":
                t["status"] = "paused"
                t["stage"] = "paused"
                t["stage_label"] = "已暂停"
                t["updated_at"] = time.time()
        batch["status"] = "paused"
        return save_batch(pid, batch)


def normalize_stopping(pid: str, bid: str) -> Optional[dict]:
    with _mutate_lock:
        batch = get_batch(pid, bid)
        if not batch:
            return None
        changed = False
        for t in batch.get("tasks", []):
            if t.get("stage") == "stopping" or t.get("stage_label") == "停止中":
                t["status"] = "paused"
                t["stage"] = "paused"
                t["stage_label"] = "已暂停"
                t["updated_at"] = time.time()
                changed = True
        if changed:
            batch["pause_requested"] = True
            batch["status"] = "paused"
            return save_batch(pid, batch)
        return batch


def clear_pause_request(pid: str, bid: str) -> Optional[dict]:
    with _mutate_lock:
        batch = get_batch(pid, bid)
        if not batch:
            return None
        batch["pause_requested"] = False
        for t in batch["tasks"]:
            if t.get("status") == "paused":
                t["status"] = "pending"
                t["stage"] = "pending"
                t["stage_label"] = ""
        return save_batch(pid, batch)


def reset_failed(pid: str, bid: str) -> Optional[dict]:
    with _mutate_lock:
        batch = get_batch(pid, bid)
        if not batch:
            return None
        for t in batch["tasks"]:
            if t.get("status") == "error" and t.get("error_dismissed_at"):
                continue
            if t["status"] in ("error", "paused", "skipped"):
                t["status"] = "pending"
                t["error"] = None
                t["attempts"] = 0
                t["stage"] = "pending"
                t["stage_label"] = ""
                t.pop("error_dismissed_at", None)
                t.pop("error_dismissed_by", None)
                t.pop("error_dismissed_reason", None)
        batch["pause_requested"] = False
        return save_batch(pid, batch)


def dismiss_task_errors(pid: str, bid: str, task_ids: list[str], *, reason: str = "user_clear") -> dict:
    with _mutate_lock:
        batch = get_batch(pid, bid)
        if not batch:
            return {"ok": False, "dismissed": 0, "batch": None}
        wanted = {str(x) for x in (task_ids or []) if str(x or "").strip()}
        now = time.time()
        dismissed = 0
        for t in batch.get("tasks", []):
            if wanted and str(t.get("id") or "") not in wanted:
                continue
            if t.get("status") != "error" and not t.get("error"):
                continue
            t["error_dismissed_at"] = now
            t["error_dismissed_by"] = "worktable"
            t["error_dismissed_reason"] = reason or "user_clear"
            t["updated_at"] = now
            dismissed += 1
        if dismissed:
            batch["last_error_dismissed_at"] = now
            batch = save_batch(pid, batch)
        return {"ok": True, "dismissed": dismissed, "batch": batch}


def delete_task_result(pid: str, bid: str, task_id: str) -> Optional[dict]:
    """Clear one generated task result without deleting the whole batch."""
    with _mutate_lock:
        batch = get_batch(pid, bid)
        if not batch:
            return None
        for t in batch.get("tasks", []):
            if str(t.get("id")) != str(task_id):
                continue
            result = t.get("result") if isinstance(t.get("result"), dict) else {}
            filename = str((result or {}).get("filename") or "")
            t["result"] = None
            t["deleted_result"] = {
                "filename": filename,
                "deleted_at": time.time(),
            }
            t["stage"] = "deleted"
            t["stage_label"] = "素材已删除"
            t["updated_at"] = time.time()
            save_batch(pid, batch)
            return {
                "task_id": t.get("id"),
                "shot_no": t.get("shot_no"),
                "filename": filename,
                "status": t.get("status"),
            }
    return None


def delete_batch(pid: str, bid: str) -> None:
    idx = [b for b in _load_index(pid) if b["id"] != bid]
    _save_index(pid, idx)
    p = _batch_path(pid, bid)
    if p.exists():
        p.unlink()
