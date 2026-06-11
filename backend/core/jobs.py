"""In-memory async job tracker (single-process).

Used for long-running operations (script decomposition, batch generation) so the
UI can poll progress without blocking the request. Jobs are ephemeral; durable
results are written to the project/batch files by the worker itself.
"""

import threading
import time
import uuid
from typing import Any, Callable, Optional

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def create(kind: str, meta: Optional[dict] = None) -> str:
    job_id = uuid.uuid4().hex[:12]
    with _lock:
        _jobs[job_id] = {
            "id": job_id,
            "kind": kind,
            "status": "running",
            "progress": 0,
            "total": 0,
            "message": "",
            "result": None,
            "error": None,
            "meta": meta or {},
            "created_at": time.time(),
        }
    return job_id


def update(job_id: str, **fields: Any) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job:
            job.update(fields)


def get(job_id: str) -> Optional[dict]:
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def run_async(kind: str, worker: Callable[[str], Any], meta: Optional[dict] = None) -> str:
    """Start `worker(job_id)` in a thread. Worker sets result via finish()/fail()."""
    job_id = create(kind, meta)

    def _run():
        try:
            result = worker(job_id)
            finish(job_id, result)
        except Exception as e:  # noqa: BLE001
            import logging
            logging.getLogger("batch_studio").exception("[Job %s] worker failed", job_id)
            fail(job_id, str(e))

    threading.Thread(target=_run, daemon=True).start()
    return job_id


def finish(job_id: str, result: Any) -> None:
    update(job_id, status="done", result=result, progress=_jobs.get(job_id, {}).get("total", 0) or 100)


def fail(job_id: str, error: str) -> None:
    import logging
    logging.getLogger("batch_studio").error(f"[Job {job_id}] 失败: {error}")
    update(job_id, status="error", error=error)
