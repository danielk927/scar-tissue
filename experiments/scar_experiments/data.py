"""Reads of the tables the learn-steps consume (app.failures and app.runs from Person 2, app.tasks) and the short
run summaries shown to the reflection LM (SPEC 9.10)."""

from typing import TypeVar

import psycopg
from pydantic import BaseModel
from scar_schemas.models import FailureRecord, RunResult, Task
from scar_schemas.openai_format import dumps_args

from .errors import InvalidRequestError, NotFoundError

MAX_SUMMARY_STEPS = 12  # same cap as the Jev state (SPEC 9.4)
EXCERPT_CHARS = 200

M = TypeVar("M", bound=BaseModel)


def _load(conn: psycopg.Connection, sql: str, ids: list[str], model: type[M], what: str) -> dict[str, M]:
    rows = conn.execute(sql, (sorted(set(ids)),)).fetchall()
    found = {key: model.model_validate(body) for key, body in rows}
    missing = sorted(set(ids) - found.keys())
    if missing:
        raise NotFoundError(f"unknown {what}: {missing[0]}")
    return found


def load_included_failures(conn: psycopg.Connection, failure_ids: list[str]) -> list[FailureRecord]:
    """The requested failures, deduplicated and sorted by failure_id. Every one must exist and be included."""
    found = _load(
        conn,
        "SELECT failure_id, body FROM app.failures WHERE failure_id = ANY(%s) ORDER BY failure_id",
        failure_ids,
        FailureRecord,
        "failure",
    )
    failures = [found[f] for f in sorted(found)]
    excluded = [f.failure_id for f in failures if not f.included]
    if excluded:
        raise InvalidRequestError(f"failure is not included: {excluded[0]}")
    return failures


def load_runs(conn: psycopg.Connection, run_ids: list[str]) -> dict[str, RunResult]:
    sql = "SELECT run_id, body FROM app.runs WHERE run_id = ANY(%s) ORDER BY run_id"
    return _load(conn, sql, run_ids, RunResult, "run")


def load_tasks(conn: psycopg.Connection, task_ids: list[str]) -> dict[str, Task]:
    sql = "SELECT task_id, body FROM app.tasks WHERE task_id = ANY(%s) ORDER BY task_id"
    return _load(conn, sql, task_ids, Task, "task")


def _clip(text: str, limit: int = EXCERPT_CHARS) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit] + "..."


def steps_summary(run: RunResult) -> str:
    """One numbered line per tool call (arguments and a result excerpt), at most MAX_SUMMARY_STEPS lines."""
    results = {m.tool_call_id: m.content or "" for m in run.messages if m.role == "tool"}
    steps = []
    for m in run.messages:
        if m.role != "assistant":
            continue
        if not m.tool_calls:
            steps.append(f"replied without a tool call: {_clip(m.content or '')}")
            continue
        for call in m.tool_calls:
            raw = call.arguments.get("__raw__") if set(call.arguments) == {"__raw__"} else None
            step = _clip(f"{call.name}({raw if raw is not None else dumps_args(call.arguments)})")
            if call.name != "submit_answer":
                step += f" -> {_clip(results.get(call.id, '(no result)'))}"
            steps.append(step)
    lines = [f"{i}. {step}" for i, step in enumerate(steps[:MAX_SUMMARY_STEPS], 1)]
    if len(steps) > MAX_SUMMARY_STEPS:
        lines.append(f"... and {len(steps) - MAX_SUMMARY_STEPS} more steps")
    return "\n".join(lines) or "(no steps)"


def outcome(run: RunResult) -> str:
    answer = dumps_args(run.final_answer) if run.final_answer is not None else "none"
    return f"final answer: {answer}; stop reason: {run.stop_reason}"
