"""Promotion gate (SPEC 9.9, P3-09).

- promotion_rule: promote iff eval_score > parent_eval_score and regression_score >= REGRESSION_MIN_PASS.
- eval_score runs every task in `eval_task_ids` once at EVAL_TEMPERATURE with purpose="eval" and returns
  passed / len(eval_task_ids). It reuses eval runs already stored for the same (task, model_version,
  prompt_version) so a crashed gate resumes and generation 0's base score is computed once for all lineages.
- regression_task_ids: up to REGRESSION_SET_SIZE train-split tasks the parent passed in its most recent traffic
  runs of the lineage, chosen with random.Random(seed * 100 + generation).
- run_gate scores the candidate on both sets (regression at temperature 0, purpose="regression") and applies the rule.

Details:
- Reuse takes the earliest stored run per task and skips runs that ended in model_error, tool_error or timeout.
  Eval reuse spans every arm, seed and generation (key: task, model_version, prompt_version, purpose "eval",
  temperature EVAL_TEMPERATURE); regression reuse is limited to this (arm, seed, generation, candidate).
  A fresh run that ends in an infrastructure failure counts as not passed; a batch lost entirely to them raises
  DependencyError.
- "Most recent traffic runs" = the parent's (model + prompt) traffic runs of the lineage in its latest generation
  <= `generation`. The candidates are sorted; the sample is returned sorted.
- An empty regression set (the parent passed no traffic task) scores 1.0, so it never blocks promotion.
"""

import random
from dataclasses import dataclass

import psycopg
from scar_schemas.constants import EVAL_TEMPERATURE, REGRESSION_MIN_PASS, REGRESSION_SET_SIZE
from scar_schemas.models import RunRequest

from .clients import AgentClient
from .data_builder import INFRA_STOP_REASONS, run_requests
from .log import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class GateResult:
    eval_score: float
    regression_score: float
    promote: bool


def promotion_rule(eval_score: float, parent_eval_score: float, regression_score: float) -> bool:
    return eval_score > parent_eval_score and regression_score >= REGRESSION_MIN_PASS


def eval_score(
    conn: psycopg.Connection,
    agent: AgentClient,
    *,
    model_version: str,
    prompt_version: str,
    arm: str,
    seed: int,
    generation: int,
    eval_task_ids: list[str],
) -> float:
    stored = _earliest(
        conn,
        "task_id = ANY(%s) AND model_version = %s AND prompt_version = %s AND purpose = 'eval'"
        " AND (body->>'temperature')::float8 = %s",
        (eval_task_ids, model_version, prompt_version, EVAL_TEMPERATURE),
    )
    score = _score(
        agent,
        eval_task_ids,
        stored,
        model_version=model_version,
        prompt_version=prompt_version,
        arm=arm,
        seed=seed,
        generation=generation,
        purpose="eval",
        temperature=EVAL_TEMPERATURE,
    )
    log.info(
        "eval_score: %.4f over %d tasks (%d reused)",
        score,
        len(eval_task_ids),
        len(stored),
        extra={"arm": arm, "seed": seed, "generation": generation, "model_version": model_version},
    )
    return score


def regression_task_ids(
    conn: psycopg.Connection,
    *,
    arm: str,
    seed: int,
    generation: int,
    parent_model_version: str,
    parent_prompt_version: str,
) -> list[str]:
    parent = (arm, seed, parent_model_version, parent_prompt_version)
    latest = conn.execute(
        "SELECT max(generation) FROM app.runs WHERE purpose = 'traffic' AND arm = %s AND seed = %s"
        " AND model_version = %s AND prompt_version = %s AND generation <= %s",
        (*parent, generation),
    ).fetchone()[0]
    if latest is None:
        return []
    rows = conn.execute(
        "SELECT DISTINCT task_id FROM app.runs WHERE purpose = 'traffic' AND passed AND split = 'train'"
        " AND arm = %s AND seed = %s AND model_version = %s AND prompt_version = %s AND generation = %s",
        (*parent, latest),
    ).fetchall()
    candidates = sorted(t for (t,) in rows)
    if len(candidates) <= REGRESSION_SET_SIZE:
        return candidates
    return sorted(random.Random(seed * 100 + generation).sample(candidates, REGRESSION_SET_SIZE))


def run_gate(
    conn: psycopg.Connection,
    agent: AgentClient,
    *,
    arm: str,
    seed: int,
    generation: int,
    candidate_model_version: str,
    candidate_prompt_version: str,
    parent_model_version: str,
    parent_prompt_version: str,
    parent_eval_score: float,
    eval_task_ids: list[str],
) -> GateResult:
    candidate = {"model_version": candidate_model_version, "prompt_version": candidate_prompt_version}
    lineage = {"arm": arm, "seed": seed, "generation": generation}
    score = eval_score(conn, agent, **candidate, **lineage, eval_task_ids=eval_task_ids)

    reg_ids = regression_task_ids(
        conn, **lineage, parent_model_version=parent_model_version, parent_prompt_version=parent_prompt_version
    )
    reg_score = 1.0
    if reg_ids:
        stored = _earliest(
            conn,
            "task_id = ANY(%s) AND model_version = %s AND prompt_version = %s AND purpose = 'regression'"
            " AND arm = %s AND seed = %s AND generation = %s",
            (reg_ids, candidate_model_version, candidate_prompt_version, arm, seed, generation),
        )
        reg_score = _score(agent, reg_ids, stored, **candidate, **lineage, purpose="regression", temperature=0.0)

    result = GateResult(score, reg_score, promotion_rule(score, parent_eval_score, reg_score))
    log.info(
        "run_gate: eval %.4f vs parent %.4f, regression %.4f over %d tasks -> %s",
        score,
        parent_eval_score,
        reg_score,
        len(reg_ids),
        "promote" if result.promote else "reject",
        extra={**lineage, "model_version": candidate_model_version},
    )
    return result


def _earliest(conn: psycopg.Connection, where: str, params: tuple) -> dict[str, bool]:
    """task_id -> passed of the earliest stored run matching `where`, skipping infrastructure failures."""
    rows = conn.execute(
        f"SELECT task_id, passed FROM app.runs WHERE {where} AND stop_reason <> ALL(%s)"
        ' ORDER BY created_at, run_id COLLATE "C"',
        (*params, list(INFRA_STOP_REASONS)),
    ).fetchall()
    passed: dict[str, bool] = {}
    for task_id, ok in rows:
        passed.setdefault(task_id, ok)
    return passed


def _score(agent: AgentClient, task_ids: list[str], stored: dict[str, bool], **request_fields) -> float:
    """passed / len(task_ids), running each task without a stored result once."""
    passed = dict(stored)
    missing = [t for t in task_ids if t not in stored]
    if missing:
        for run in run_requests(agent, [RunRequest(task_id=t, n=1, **request_fields) for t in missing]):
            passed.setdefault(run.task_id, run.passed)
    return sum(passed.get(t, False) for t in task_ids) / len(task_ids)
