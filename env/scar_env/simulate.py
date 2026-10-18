"""Traffic simulator (SPEC 9.5, P1-11): one generation of traffic from the train pool, run by the agent service.

- Positions: random.Random(1000 * seed + generation).sample(range(TRAIN_POOL_SIZE), n_tasks). The arm is deliberately
  not part of the seed, so every arm sees the same tasks for a seed and generation.
- One agent POST /agent/run_batch with a RunRequest per task, in sampled order: purpose "traffic", temperature 0.0,
  n=1, and the request's arm, seed, generation, model_version and prompt_version.
- Resume: traffic runs already stored with the same arm, seed, generation, model_version, prompt_version and task are
  reused (the earliest per task) unless they ended for infrastructure reasons, and only the other tasks are sent to the
  agent. The agent stores each run as it finishes, so a call that failed midway keeps its finished runs. A second call
  with the same key waits for the first to finish (in this process), then reuses its runs.
- A batch whose runs all ended for infrastructure reasons is an outage: 503, and a retry runs those tasks again.
"""

import asyncio
import math
import random
from collections import defaultdict

import httpx
import psycopg
from fastapi.concurrency import run_in_threadpool
from pydantic import TypeAdapter, ValidationError
from scar_schemas.constants import TRAIN_POOL_SIZE
from scar_schemas.models import RunBatchRequest, RunRequest, RunResult, SimulateRequest, SimulateResult

from .db import Database
from .errors import ApiError
from .log import get_logger

MAX_CONCURRENCY = 16  # agent attempts in flight per batch (RunBatchRequest allows 1..64, default 8)
RUN_TIMEOUT_S = 180  # SPEC 7.4: whole run
INFRA_STOP_REASONS = ("model_error", "tool_error", "timeout")  # runs SPEC 9.4 never learns from
RUNS = TypeAdapter(list[RunResult])

log = get_logger(__name__)
_in_flight: defaultdict[tuple, asyncio.Lock] = defaultdict(asyncio.Lock)


def schedule(seed: int, generation: int, n_tasks: int) -> list[int]:
    return random.Random(1000 * seed + generation).sample(range(TRAIN_POOL_SIZE), n_tasks)


def batch_timeout(n_runs: int) -> float:
    """Seconds to wait for the agent's reply: every wave of MAX_CONCURRENCY runs hitting the run timeout, plus one."""
    return (math.ceil(n_runs / MAX_CONCURRENCY) + 1) * RUN_TIMEOUT_S


async def simulate(db: Database, agent: httpx.AsyncClient, req: SimulateRequest) -> SimulateResult:
    if not 1 <= req.n_tasks <= TRAIN_POOL_SIZE:
        raise ApiError(422, "invalid_n_tasks", f"n_tasks must be 1..{TRAIN_POOL_SIZE}, not {req.n_tasks}")

    def read() -> tuple[list[str], dict[str, tuple[str, bool]]]:
        with db.conn() as conn:
            task_ids = sampled_task_ids(conn, req)
            return task_ids, stored_runs(conn, req, task_ids)

    async with _in_flight[(req.arm, req.seed, req.generation, req.model_version, req.prompt_version)]:
        task_ids, runs = await run_in_threadpool(read)  # no DB connection is held while the agent works
        missing = [t for t in task_ids if t not in runs]
        log.info(
            "simulate: %d of %d traffic runs already stored, running %d",
            len(task_ids) - len(missing),
            len(task_ids),
            len(missing),
            extra={"arm": req.arm, "seed": req.seed, "generation": req.generation, "model_version": req.model_version},
        )
        if missing:
            for run in await run_batch(agent, batch_request(req, missing)):
                runs[run.task_id] = (run.run_id, run.passed)
    passed = sum(runs[t][1] for t in task_ids)
    return SimulateResult(run_ids=[runs[t][0] for t in task_ids], passed=passed, failed=len(task_ids) - passed)


def sampled_task_ids(conn: psycopg.Connection, req: SimulateRequest) -> list[str]:
    """Task IDs at the scheduled pool positions, in sampled order. 409 unless the pool is complete."""
    pool = dict(conn.execute("SELECT position, task_id FROM app.train_pool ORDER BY position").fetchall())
    if len(pool) != TRAIN_POOL_SIZE:
        raise ApiError(
            409,
            "train_pool_incomplete",
            f"app.train_pool has {len(pool)} of {TRAIN_POOL_SIZE} positions; it is built once by"
            " POST /tasks/generate with origin=generated (P1-08)",
        )
    return [pool[p] for p in schedule(req.seed, req.generation, req.n_tasks)]


def stored_runs(conn: psycopg.Connection, req: SimulateRequest, task_ids: list[str]) -> dict[str, tuple[str, bool]]:
    """task_id -> (run_id, passed) of the earliest reusable stored traffic run with the request's key."""
    rows = conn.execute(
        "SELECT task_id, run_id, passed FROM app.runs WHERE purpose = 'traffic' AND arm = %s AND seed = %s"
        " AND generation = %s AND model_version = %s AND prompt_version = %s AND task_id = ANY(%s)"
        " AND stop_reason <> ALL(%s) ORDER BY created_at, run_id",
        (req.arm, req.seed, req.generation, req.model_version, req.prompt_version, task_ids, list(INFRA_STOP_REASONS)),
    ).fetchall()
    runs: dict[str, tuple[str, bool]] = {}
    for task_id, run_id, passed in rows:
        runs.setdefault(task_id, (run_id, passed))
    return runs


def batch_request(req: SimulateRequest, task_ids: list[str]) -> RunBatchRequest:
    return RunBatchRequest(
        requests=[
            RunRequest(
                task_id=task_id,
                model_version=req.model_version,
                prompt_version=req.prompt_version,
                arm=req.arm,
                seed=req.seed,
                generation=req.generation,
                purpose="traffic",
                temperature=0.0,
                n=1,
            )
            for task_id in task_ids
        ],
        max_concurrency=MAX_CONCURRENCY,
    )


async def run_batch(agent: httpx.AsyncClient, batch: RunBatchRequest) -> list[RunResult]:
    """Agent POST /agent/run_batch: one run per request, in request order. A 404 (unknown model_version,
    prompt_version or task) passes through. Unreachable, timed out, any other non-200, a reply that does not match the
    batch, or a batch lost entirely to infrastructure is a 503."""
    where = "agent /agent/run_batch"
    try:
        resp = await agent.post(
            "/agent/run_batch", json=batch.model_dump(mode="json"), timeout=batch_timeout(len(batch.requests))
        )
    except httpx.HTTPError as exc:
        raise ApiError(503, "agent_unavailable", f"{where}: {exc!r}") from exc
    if resp.status_code == 404:
        raise ApiError(404, "not_found", f"{where}: {resp.text[:300]}")
    if resp.status_code != 200:
        raise ApiError(503, "agent_unavailable", f"{where}: HTTP {resp.status_code} {resp.text[:300]}")
    try:
        runs = RUNS.validate_json(resp.content)
    except ValidationError as exc:
        raise ApiError(503, "agent_unavailable", f"{where}: malformed reply: {exc}") from exc
    if [r.task_id for r in runs] != [r.task_id for r in batch.requests]:
        raise ApiError(
            503, "agent_unavailable", f"{where}: {len(runs)} runs do not match the {len(batch.requests)} sent"
        )
    if all(r.stop_reason in INFRA_STOP_REASONS for r in runs):
        raise ApiError(
            503, "agent_unavailable", f"{where}: all {len(runs)} runs ended in {'/'.join(INFRA_STOP_REASONS)}"
        )
    return runs
