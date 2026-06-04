"""Per-episode concurrent batch orchestration.

Runs a set of per-episode batches with bounded parallelism:

  - **Within an episode: serial.** Each episode's batch is created with
    ``concurrency=1`` so its shots generate one after another (and continuity
    chaining stays correct).
  - **Across episodes: parallel, capped.** A module-level ``ThreadPoolExecutor``
    sized by ``max_parallel`` runs at most N episodes at once; the rest stay
    ``pending`` (shown as "排队" in the UI) until a slot frees up.

The cap is the primary memory guard: at any moment at most ``max_parallel``
shots are in flight, each holding only its own reference images, so RAM stays
bounded no matter how many episodes are queued (avoids OOM).
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..core import jobs
from . import batch_engine

logger = logging.getLogger("batch_studio")


def start(pid: str, batch_ids: list[str], max_parallel: int = 2) -> str:
    """Kick off orchestrated generation; returns a job id for progress polling.

    Episode-level progress is also observable via the per-batch status files
    (the worktable polls those), so this job is mainly a lifecycle handle.
    """
    cap = max(1, int(max_parallel or 1))
    ids = [b for b in (batch_ids or []) if b]

    def worker(job_id):
        total = len(ids)
        done = 0
        jobs.update(job_id, total=total, progress=0, message=f"0/{total} 集完成")
        results: list[dict] = []
        with ThreadPoolExecutor(max_workers=cap, thread_name_prefix="ep") as pool:
            futs = {pool.submit(batch_engine.run_batch, pid, bid): bid for bid in ids}
            for fut in as_completed(futs):
                bid = futs[fut]
                try:
                    results.append({"batch_id": bid, "result": fut.result()})
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[EpisodeRunner] 批次 {bid} 失败: {e}")
                    results.append({"batch_id": bid, "error": str(e)})
                done += 1
                jobs.update(job_id, progress=done, message=f"{done}/{total} 集完成")
        return {"episodes": total, "max_parallel": cap, "results": results}

    return jobs.run_async("episode_batches", worker,
                          meta={"project": pid, "batches": ids, "max_parallel": cap})
