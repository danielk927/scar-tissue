"""Learn-step C, GEPA (SPEC 9.10, F17, P1-13): optimize the system prompt with `gepa` (pinned 0.1.4) on this
generation's variants plus train-pool tasks, scoring candidates with real agent runs.

`conn` must be an autocommit connection. A GEPA run makes about GEPA_MAX_METRIC_CALLS agent runs and can take many
minutes.
"""

import logging
import random
from datetime import datetime, timezone

import gepa
import psycopg
from scar_schemas.constants import (
    EVAL_TEMPERATURE,
    GEPA_MAX_METRIC_CALLS,
    GEPA_TRAIN_EXTRA,
    GEPA_VAL_SIZE,
    VARIANTS_PER_FAILURE,
)
from scar_schemas.guards import assert_train_only
from scar_schemas.models import (
    LearnRequest,
    LearnResult,
    MutateRequest,
    PromptVersion,
    RunBatchRequest,
    RunRequest,
    RunResult,
    Task,
)

from .clients import AgentClient, EnvClient, ReflectionLM
from .data import load_included_failures, load_tasks, outcome, steps_summary
from .errors import DependencyError, InvalidRequestError, StateConflictError
from .prompts import candidate_version_id, learned_result, prompt_text, store_prompt_version

COMPONENT = "system_prompt"
# Runs that ended for infrastructure reasons, not the agent's choices (the same set SPEC 9.4 never learns from).
INFRA_STOP_REASONS = frozenset({"model_error", "tool_error", "timeout"})

log = logging.getLogger(__name__)


class AgentPromptAdapter(gepa.GEPAAdapter):
    """Scores a candidate system prompt by running the agent on a batch of tasks: 1.0 if passed, else 0.0.

    Subclassing gepa.GEPAAdapter is required: it supplies `propose_new_texts = None`. Without it gepa 0.1.4 hits an
    AttributeError in every iteration, swallows it, and silently never mutates (SPEC F17).
    """

    def __init__(self, conn: psycopg.Connection, agent: AgentClient, req: LearnRequest):
        self.conn = conn
        self.agent = agent
        self.req = req
        self.agent_runs = 0
        self.infra_failures = 0

    def _candidate_version(self, text: str) -> str:
        """Store the candidate as PromptVersion C-cand-<hex> (reused if it exists) so the agent can load it."""
        pv = PromptVersion(
            prompt_version=candidate_version_id(text),
            arm="C",
            seed=self.req.seed,
            generation=self.req.generation,
            text=text,
            parent_prompt_version=self.req.current_prompt_version,
            is_candidate=True,
            created_at=datetime.now(timezone.utc),
        )
        return store_prompt_version(self.conn, pv).prompt_version

    def evaluate(
        self, batch: list[Task], candidate: dict[str, str], capture_traces: bool = False
    ) -> gepa.EvaluationBatch:
        version = self._candidate_version(candidate[COMPONENT])
        requests = [
            RunRequest(
                task_id=task.task_id,
                model_version=self.req.model_version,
                prompt_version=version,
                arm="C",
                seed=self.req.seed,
                generation=self.req.generation,
                purpose="gepa",
                temperature=EVAL_TEMPERATURE,
            )
            for task in batch
        ]
        runs = self.agent.run_batch(RunBatchRequest(requests=requests))
        if [r.task_id for r in runs] != [t.task_id for t in batch]:
            raise RuntimeError("agent /agent/run_batch returned runs that do not match the requests")
        self.agent_runs += len(runs)
        infra = sum(r.stop_reason in INFRA_STOP_REASONS for r in runs)
        # A whole batch lost to infrastructure is an outage: raise (gepa propagates it) rather than score it 0.
        if infra == len(runs):
            reasons = ", ".join(sorted({r.stop_reason for r in runs}))
            raise DependencyError(f"all {len(runs)} agent runs in a GEPA batch ended in {reasons}")
        self.infra_failures += infra
        return gepa.EvaluationBatch(
            outputs=runs,
            scores=[1.0 if r.passed else 0.0 for r in runs],
            trajectories=[_trajectory(t, r) for t, r in zip(batch, runs, strict=True)] if capture_traces else None,
        )

    def make_reflective_dataset(
        self, candidate: dict[str, str], eval_batch: gepa.EvaluationBatch, components_to_update: list[str]
    ) -> dict[str, list[dict]]:
        assert eval_batch.trajectories is not None
        return {COMPONENT: list(eval_batch.trajectories)}


def _trajectory(task: Task, run: RunResult) -> dict:
    """One reflective record: the question, what the agent submitted, and the verifier detail plus steps."""
    verdict = "Passed" if run.passed else "Failed"
    return {
        "Inputs": {"question": task.prompt},
        "Generated Outputs": outcome(run),
        "Feedback": f"{verdict}. Verifier: {run.verify_detail}\nSteps:\n{steps_summary(run)}",
    }


class _ReflectionGuard:
    """gepa 0.1.4 logs and swallows reflection-LM exceptions and would finish with the unchanged seed prompt.
    This wrapper keeps the first exception, and `stop` (a gepa stop callback) ends the run so it can be raised."""

    def __init__(self, lm: ReflectionLM):
        self.lm = lm
        self.error: Exception | None = None

    def __call__(self, prompt: str | list[dict]) -> str:
        try:
            return self.lm(prompt)
        except Exception as e:
            self.error = self.error or e
            raise

    def stop(self, _state: object) -> bool:
        return self.error is not None


class _GepaLog:
    """Routes gepa's progress messages to logging (its default logger prints to stdout)."""

    def log(self, message: str) -> None:
        log.info(message)


def _variants(conn: psycopg.Connection, failure_ids: list[str]) -> list[Task]:
    rows = conn.execute(
        "SELECT body FROM app.tasks WHERE origin = 'variant' AND parent_failure_id = ANY(%s) ORDER BY task_id",
        (failure_ids,),
    ).fetchall()
    return [Task.model_validate(body) for (body,) in rows]


def gepa_sets(
    conn: psycopg.Connection, req: LearnRequest, failure_ids: list[str], env: EnvClient
) -> tuple[list[Task], list[Task]]:
    """Trainset: the variants of `failure_ids` (env POST /mutate makes them for failures that have none) plus
    GEPA_TRAIN_EXTRA train-pool tasks. Valset: GEPA_VAL_SIZE other train-pool tasks.

    Pool tasks are one draw from the pool in position order with random.Random(f"gepa:{seed}:{generation}"): the
    first GEPA_TRAIN_EXTRA go to the trainset, the rest to the valset, so the two are disjoint.
    """
    variants = _variants(conn, failure_ids)
    missing = sorted(set(failure_ids) - {v.parent_failure_id for v in variants})
    if missing:
        env.mutate(MutateRequest(failure_ids=missing, variants_per_failure=VARIANTS_PER_FAILURE, seed=req.seed))
        variants = _variants(conn, failure_ids)

    variant_ids = {v.task_id for v in variants}
    rows = conn.execute("SELECT task_id FROM app.train_pool ORDER BY position").fetchall()
    pool = [tid for (tid,) in rows if tid not in variant_ids]
    need = GEPA_TRAIN_EXTRA + GEPA_VAL_SIZE
    if len(pool) < need:
        raise StateConflictError(f"train pool has {len(pool)} usable tasks; GEPA needs {need}")
    drawn = random.Random(f"gepa:{req.seed}:{req.generation}").sample(pool, need)
    tasks = load_tasks(conn, drawn)
    trainset = variants + [tasks[tid] for tid in drawn[:GEPA_TRAIN_EXTRA]]
    valset = [tasks[tid] for tid in drawn[GEPA_TRAIN_EXTRA:]]
    return trainset, valset


def learn_c(
    conn: psycopg.Connection,
    req: LearnRequest,
    reflection_lm: ReflectionLM,
    agent: AgentClient,
    env: EnvClient,
    system_v1: str,
) -> LearnResult:
    if req.arm != "C":
        raise InvalidRequestError(f"/learn/C needs arm C, got {req.arm}")
    version = f"C-s{req.seed}-g{req.generation}"
    if (done := learned_result(conn, version, req)) is not None:
        return done

    seed_text = prompt_text(conn, req.current_prompt_version, system_v1)
    failures = load_included_failures(conn, req.failure_ids)
    assert_train_only([f.task_id for f in failures], conn)
    trainset, valset = gepa_sets(conn, req, [f.failure_id for f in failures], env)
    assert_train_only([t.task_id for t in trainset + valset], conn)

    adapter = AgentPromptAdapter(conn, agent, req)
    guard = _ReflectionGuard(reflection_lm)
    result = gepa.optimize(
        seed_candidate={COMPONENT: seed_text},
        trainset=trainset,
        valset=valset,
        adapter=adapter,
        reflection_lm=guard,
        max_metric_calls=GEPA_MAX_METRIC_CALLS,
        stop_callbacks=guard.stop,
        seed=req.seed,
        logger=_GepaLog(),
    )
    if guard.error is not None:
        raise guard.error
    best = result.best_candidate
    assert isinstance(best, dict)
    stored = store_prompt_version(
        conn,
        PromptVersion(
            prompt_version=version,
            arm="C",
            seed=req.seed,
            generation=req.generation,
            text=best[COMPONENT],
            parent_prompt_version=req.current_prompt_version,
            is_candidate=False,
            created_at=datetime.now(timezone.utc),
        ),
    )
    scores = result.val_aggregate_scores
    notes = (
        f"GEPA: {len(result.candidates)} candidates, best valset score {scores[result.best_idx]:.3f}"
        f" (seed prompt {scores[0]:.3f}); {adapter.agent_runs} agent runs"
        f" ({adapter.infra_failures} ended in model_error, tool_error or timeout);"
        f" trainset {len(trainset)} ({len(trainset) - GEPA_TRAIN_EXTRA} variants), valset {len(valset)}"
    )
    return LearnResult(prompt_version=stored.prompt_version, notes=notes)
