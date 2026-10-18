"""HTTP clients for the env and agent services (SPEC 8.5). Bodies are the shared models.

Any transport failure or non-2xx answer raises DependencyError (the loop marks the step failed and can resume).
Timeouts are long: one call can run a whole batch of agent runs (SPEC 7.4: a run can take 180 s).
"""

import math
import threading

import httpx
from pydantic import TypeAdapter
from scar_schemas.models import (
    EvalSet,
    FailureRecord,
    LearnRequest,
    LearnResult,
    MineRequest,
    MutateRequest,
    RunBatchRequest,
    RunResult,
    SimulateRequest,
    SimulateResult,
    Task,
)

RUN_TIMEOUT_S = 180

# One Veri replica serves every agent run. Lineages take turns on it one batch at a time: two overlapping batches
# double its load, model calls time out, and scores measured under different loads stop being comparable.
REPLICA_LOCK = threading.Lock()


class DependencyError(RuntimeError):
    """A service the loop depends on failed or answered with an error."""


def batch_timeout(n_runs: int, concurrency: int) -> float:
    """Generous wall-clock bound for a batch: one 180 s run per concurrency slot, plus one spare round."""
    return (math.ceil(max(n_runs, 1) / max(concurrency, 1)) + 1) * RUN_TIMEOUT_S


class _Client:
    def __init__(self, base_url: str, transport: httpx.BaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.transport = transport

    def _call(self, method: str, path: str, *, json: dict | None = None, timeout: float = 30.0) -> object:
        try:
            with httpx.Client(base_url=self.base_url, transport=self.transport, timeout=timeout) as http:
                resp = http.request(method, path, json=json)
        except httpx.HTTPError as e:
            raise DependencyError(f"{method} {self.base_url}{path}: {e!r}") from e
        if resp.status_code >= 300:
            raise DependencyError(f"{method} {self.base_url}{path}: HTTP {resp.status_code} {resp.text[:500]}")
        return resp.json()


class AgentClient(_Client):
    def run_batch(self, req: RunBatchRequest) -> list[RunResult]:
        n = sum(r.n for r in req.requests)
        with REPLICA_LOCK:
            body = self._call(
                "POST",
                "/agent/run_batch",
                json=req.model_dump(mode="json"),
                timeout=batch_timeout(n, req.max_concurrency),
            )
        return TypeAdapter(list[RunResult]).validate_python(body)

    def mine(self, req: MineRequest) -> list[FailureRecord]:
        body = self._call("POST", "/failures/mine", json=req.model_dump(mode="json"), timeout=1800)
        return TypeAdapter(list[FailureRecord]).validate_python(body)


class EnvClient(_Client):
    def simulate(self, req: SimulateRequest) -> SimulateResult:
        timeout = batch_timeout(req.n_tasks, 16) + 60  # env runs the batch at concurrency 16
        with REPLICA_LOCK:
            body = self._call("POST", "/simulate", json=req.model_dump(mode="json"), timeout=timeout)
        return SimulateResult.model_validate(body)

    def mutate(self, req: MutateRequest) -> list[Task]:
        body = self._call("POST", "/mutate", json=req.model_dump(mode="json"), timeout=600)
        return TypeAdapter(list[Task]).validate_python(body)

    def learn(self, req: LearnRequest) -> LearnResult:
        # /learn/C runs GEPA (up to GEPA_MAX_METRIC_CALLS agent runs); allow hours, not minutes.
        return LearnResult.model_validate(
            self._call("POST", f"/learn/{req.arm}", json=req.model_dump(mode="json"), timeout=4 * 3600)
        )

    def eval_set(self, eval_set_id: str) -> EvalSet:
        return EvalSet.model_validate(self._call("GET", f"/eval_sets/{eval_set_id}"))
