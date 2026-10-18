"""POST /loop/run and GET /loop/status/{job_id} (SPEC 6.3, 8.5, P3-10).

A job runs its generations in order in a background thread and stops at the first failure. One job per lineage at a
time (409 otherwise). Jobs live in memory, so a restart forgets them; the generation records in the database make
the next run of the same generations resume where they stopped.
"""

import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from scar_schemas.ids import new_id
from scar_schemas.models import Arm

from .. import loop, registry
from ..deps import DbDep
from ..errors import ApiError, not_found
from ..log import get_logger

router = APIRouter()
log = get_logger(__name__)

LOG_TAIL = 50


class LoopRunRequest(BaseModel):
    arm: Arm
    seed: int
    generations: list[int]
    tiny: bool = False


@dataclass
class Job:
    job_id: str
    lineage: str
    status: str = "queued"  # queued, running, done, failed
    current_generation_id: str | None = None
    log: deque[str] = field(default_factory=lambda: deque(maxlen=LOG_TAIL))

    def add_log(self, msg: str) -> None:
        self.log.append(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}")


_jobs: dict[str, Job] = {}
_busy: dict[str, str] = {}  # lineage -> job_id of its queued or running job
_lock = threading.Lock()


def get_loop_context() -> loop.LoopContext:
    try:
        return loop.default_context()
    except (NotImplementedError, ValueError) as e:
        raise ApiError(503, "trainer_unavailable", str(e)) from e


LoopContextDep = Annotated[loop.LoopContext, Depends(get_loop_context)]


@router.post("/loop/run")
def run_loop(req: LoopRunRequest, db: DbDep, ctx: LoopContextDep) -> dict:
    loop.check_backend(ctx, req.arm, req.tiny)
    lin = registry.lineage(req.arm, req.seed)
    with _lock:
        if lin in _busy:
            raise ApiError(409, "lineage_busy", f"job {_busy[lin]} is already running lineage {lin}")
        with db.conn() as conn:
            loop.check_run(conn, req.arm, req.seed, req.generations, req.tiny)
        job = Job(job_id=new_id("job"), lineage=lin)
        _jobs[job.job_id] = job
        _busy[lin] = job.job_id
    job.add_log(f"queued {lin} generations {req.generations}{' (tiny)' if req.tiny else ''}")
    try:
        threading.Thread(target=_run_job, args=(job, req, db.url, ctx), name=f"loop-{lin}", daemon=True).start()
    except RuntimeError:  # no thread could start: free the lineage
        with _lock:
            _busy.pop(lin, None)
            job.status = "failed"
        raise
    return {"job_id": job.job_id}


@router.get("/loop/status/{job_id}")
def loop_status(job_id: str) -> dict:
    job = _jobs.get(job_id)
    if job is None:
        raise not_found("job", job_id)
    return {"status": job.status, "current_generation_id": job.current_generation_id, "log_tail": list(job.log)}


def _run_job(job: Job, req: LoopRunRequest, db_url: str, ctx: loop.LoopContext) -> None:
    status = "failed"
    job.status = "running"
    try:
        # Autocommit: every record write commits at once, and no transaction stays open during long agent batches.
        with psycopg.connect(db_url, autocommit=True) as conn:
            for g in req.generations:
                job.current_generation_id = registry.generation_id(req.arm, req.seed, g)
                loop.run_generation(conn, ctx, req.arm, req.seed, g, tiny=req.tiny, on_log=job.add_log)
        status = "done"
    except Exception as e:
        log.error("loop job %s failed", job.job_id, exc_info=e)
        job.add_log(f"job failed: {type(e).__name__}: {e}")
    finally:
        with _lock:
            _busy.pop(job.lineage, None)
            job.status = status
