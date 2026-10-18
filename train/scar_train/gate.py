"""Promotion gate (SPEC 9.9, P3-09). Interface fixed here; bodies are filled in by P3-09.

- promotion_rule: promote iff eval_score > parent_eval_score and regression_score >= REGRESSION_MIN_PASS.
- eval_score runs every task in `eval_task_ids` once at EVAL_TEMPERATURE with purpose="eval" and returns
  passed / len(eval_task_ids). It reuses eval runs already stored for the same (task, model_version,
  prompt_version) so a crashed gate resumes and generation 0's base score is computed once for all lineages.
- regression_task_ids: up to REGRESSION_SET_SIZE train-split tasks the parent passed in its most recent traffic
  runs of the lineage, chosen with random.Random(seed * 100 + generation).
- run_gate scores the candidate on both sets (regression at temperature 0, purpose="regression") and applies the rule.
"""

from dataclasses import dataclass

import psycopg

from .clients import AgentClient


@dataclass(frozen=True)
class GateResult:
    eval_score: float
    regression_score: float
    promote: bool


def promotion_rule(eval_score: float, parent_eval_score: float, regression_score: float) -> bool:
    raise NotImplementedError("P3-09")


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
    raise NotImplementedError("P3-09")


def regression_task_ids(
    conn: psycopg.Connection,
    *,
    arm: str,
    seed: int,
    generation: int,
    parent_model_version: str,
    parent_prompt_version: str,
) -> list[str]:
    raise NotImplementedError("P3-09")


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
    raise NotImplementedError("P3-09")
