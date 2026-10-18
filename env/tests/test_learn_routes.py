"""POST /learn/B and /learn/C wiring: dependencies, SPEC 7.4 error bodies, and a happy path for each with fakes
(the learn-step behavior itself is tested in experiments/tests)."""

import logging
from datetime import datetime, timezone
from decimal import Decimal

import pytest
import scar_experiments.prompts
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from scar_experiments.errors import DependencyError
from scar_schemas.constants import GEPA_TRAIN_EXTRA, GEPA_VAL_SIZE
from scar_schemas.ids import new_id
from scar_schemas.models import ExpectedAnswer, FailureRecord, RunResult, Task

from scar_env.app import create_app
from scar_env.routes import learn
from scar_env.store import store_task

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
SYSTEM = "You are a careful bookkeeping assistant."
HINT = "Check the date range twice."
LM_VARS = ("REFLECTION_LM_BASE_URL", "REFLECTION_LM_API_KEY", "REFLECTION_LM_MODEL")


def _task(conn, origin="generated", parent_failure_id=None, split="train") -> Task:
    task = Task(
        task_id=new_id("task"),
        company_id="company-901",
        split=split,
        family="aggregation",
        template_id="agg.total_by_account_period",
        params={},
        prompt="How much was spent on rent in Q1 2026?",
        expected=ExpectedAnswer(answer_type="number", number=Decimal("7500.00")),
        difficulty=1,
        origin=origin,
        parent_failure_id=parent_failure_id,
        created_at=NOW,
    )
    store_task(conn, task)
    return task


def _run(task: Task, passed: bool, prompt_version="system_v1", arm="B", purpose="traffic") -> RunResult:
    return RunResult(
        run_id=new_id("run"),
        task_id=task.task_id,
        company_id=task.company_id,
        split=task.split,
        family=task.family,
        model_version="base",
        endpoint_model_id="qwen-test",
        prompt_version=prompt_version,
        tools_version="tools_v1",
        arm=arm,
        seed=0,
        generation=1,
        purpose=purpose,
        origin=task.origin,
        attempt_index=0,
        temperature=0.0,
        messages=[],
        final_answer={"number": 7500 if passed else 7600},
        stop_reason="submitted",
        passed=passed,
        verify_detail="ok" if passed else "number: expected 7500.00, got 7600",
        steps=1,
        input_tokens=10,
        output_tokens=5,
        latency_ms=100,
        sentry_trace_id=None,
        created_at=NOW,
    )


def _failure(conn, arm="B", split="train") -> FailureRecord:
    task = _task(conn, split=split)
    run = _run(task, passed=False, arm=arm)
    conn.execute(
        "INSERT INTO app.runs (run_id, task_id, model_version, prompt_version, arm, seed, generation, purpose, origin,"
        " split, family, passed, stop_reason, body, created_at) VALUES"
        " (%s, %s, 'base', 'system_v1', %s, 0, 1, 'traffic', 'generated', 'train', 'aggregation', false, 'submitted',"
        " %s, %s)",
        (run.run_id, task.task_id, arm, Jsonb(run.model_dump(mode="json")), NOW),
    )
    failure = FailureRecord(
        failure_id=new_id("fail"),
        run_id=run.run_id,
        task_id=task.task_id,
        arm=arm,
        seed=0,
        generation=1,
        model_version="base",
        origin="generated",
        cause="bad_tool_args",
        cause_confidence=0.9,
        cause_probabilities={"bad_tool_args": 0.9},
        learnable_p=0.9,
        included=True,
        exclusion_reason=None,
        jev_model="jev-1.14.0",
        created_at=NOW,
    )
    conn.execute(
        "INSERT INTO app.failures VALUES (%s, %s, %s, 0, 1, true, %s, %s)",
        (failure.failure_id, run.run_id, arm, Jsonb(failure.model_dump(mode="json")), NOW),
    )
    return failure


def _body(arm, failure_ids) -> dict:
    return {
        "arm": arm,
        "seed": 0,
        "generation": 1,
        "model_version": "base",
        "current_prompt_version": "system_v1",
        "failure_ids": failure_ids,
    }


class FakeAgent:
    """Passes a run when the stored prompt contains HINT."""

    def __init__(self, conn):
        self.conn = conn
        self.runs = 0

    def run_batch(self, req):
        out = []
        for r in req.requests:
            (text,) = self.conn.execute(
                "SELECT body->>'text' FROM app.prompt_versions WHERE prompt_version = %s", (r.prompt_version,)
            ).fetchone()
            (body,) = self.conn.execute("SELECT body FROM app.tasks WHERE task_id = %s", (r.task_id,)).fetchone()
            out.append(_run(Task.model_validate(body), HINT in text, r.prompt_version, arm="C", purpose="gepa"))
        self.runs += len(out)
        return out


class NoEnv:
    def mutate(self, req):
        raise AssertionError("variants exist, so /mutate must not be called")


def _override(client, lm, agent=None):
    overrides = client.app.dependency_overrides
    overrides[learn.get_reflection_lm] = lambda: lm
    overrides[learn.get_system_v1] = lambda: SYSTEM
    overrides[learn.get_agent_client] = lambda: agent
    overrides[learn.get_env_client] = NoEnv


@pytest.mark.parametrize("path, arm", [("/learn/B", "B"), ("/learn/C", "C")])
def test_503_when_reflection_lm_is_not_configured(client, monkeypatch, path, arm):
    for var in LM_VARS:
        monkeypatch.delenv(var, raising=False)
    r = client.post(path, json=_body(arm, []))
    assert r.status_code == 503
    assert r.json() == {
        "error": "reflection_lm_unconfigured",
        "detail": "set REFLECTION_LM_BASE_URL, REFLECTION_LM_API_KEY and REFLECTION_LM_MODEL",
    }


def test_503_when_system_v1_file_is_missing(client, monkeypatch, tmp_path):
    for var, value in zip(LM_VARS, ("http://localhost:9/v1", "unused-key", "unused-model"), strict=True):
        monkeypatch.setenv(var, value)
    monkeypatch.setattr(scar_experiments.prompts, "SYSTEM_V1_PATH", tmp_path / "missing.md")
    r = client.post("/learn/B", json=_body("B", []))
    assert r.status_code == 503 and r.json()["error"] == "system_v1_missing"
    assert "missing.md" in r.json()["detail"]


def test_learn_b_route(client, conn):
    failure = _failure(conn)
    prompts = []
    _override(client, lambda p: prompts.append(p) or "When filtering by account, do look up the ID first.")
    r = client.post("/learn/B", json=_body("B", [failure.failure_id]))
    assert r.status_code == 200
    assert r.json()["prompt_version"] == "B-s0-g1" and len(prompts) == 1
    (text,) = conn.execute("SELECT body->>'text' FROM app.prompt_versions WHERE prompt_version = 'B-s0-g1'").fetchone()
    assert text == SYSTEM + "\n\nLessons from past mistakes:\n- When filtering by account, do look up the ID first."


def test_learn_route_errors_use_spec_bodies(client, conn):
    failure = _failure(conn)

    def broken_lm(prompt):
        raise DependencyError("reflection LM call failed: connection refused")

    _override(client, broken_lm)
    r = client.post("/learn/B", json=_body("B", ["fail_missing"]))
    assert (r.status_code, r.json()) == (404, {"error": "not_found", "detail": "unknown failure: fail_missing"})
    r = client.post("/learn/C", json=_body("B", []))
    assert (r.status_code, r.json()["error"]) == (422, "invalid_input")
    r = client.post("/learn/B", json=_body("B", [failure.failure_id]))
    assert (r.status_code, r.json()["error"]) == (503, "dependency_unavailable")
    assert "connection refused" in r.json()["detail"]

    # No train pool yet: a state conflict.
    c_failure = _failure(conn, arm="C")
    _task(conn, origin="variant", parent_failure_id=c_failure.failure_id)
    r = client.post("/learn/C", json=_body("C", [c_failure.failure_id]))
    assert (r.status_code, r.json()) == (
        409,
        {"error": "conflict", "detail": "train pool has 0 usable tasks; GEPA needs 100"},
    )


def test_unexpected_errors_become_a_logged_500_with_the_spec_body(db_url, conn, caplog):
    """The app's catch-all handler (scar_env.errors), reached here by the eval-leakage guard's plain RuntimeError."""
    leaked = _failure(conn, split="eval")
    # Starlette re-raises after sending the 500, so the test client must not raise to let us see the response.
    with TestClient(create_app(db_url), raise_server_exceptions=False) as client:
        _override(client, lambda p: "When x, do y.")
        with caplog.at_level(logging.ERROR, logger="scar_env.errors"):
            r = client.post("/learn/B", json=_body("B", [leaked.failure_id]))
    assert r.status_code == 500
    assert r.json() == {
        "error": "internal_error",
        "detail": f"RuntimeError: eval leakage: 1 non-train tasks, e.g. {leaked.task_id}",
    }
    (record,) = [rec for rec in caplog.records if rec.name == "scar_env.errors"]
    assert record.getMessage() == "unhandled error on POST /learn/B"
    assert record.exc_info[0] is RuntimeError and record.exc_info[2] is not None


def test_learn_c_route(client, conn):
    failure = _failure(conn, arm="C")
    for _ in range(2):
        _task(conn, origin="variant", parent_failure_id=failure.failure_id)
    for position in range(GEPA_TRAIN_EXTRA + GEPA_VAL_SIZE):
        conn.execute("INSERT INTO app.train_pool VALUES (%s, %s)", (position, _task(conn).task_id))
    agent = FakeAgent(conn)
    _override(client, lambda p: f"```\n{SYSTEM}\n{HINT}\n```", agent)

    r = client.post("/learn/C", json=_body("C", [failure.failure_id]))
    assert r.status_code == 200
    assert r.json()["prompt_version"] == "C-s0-g1" and agent.runs > 0
    (text,) = conn.execute("SELECT body->>'text' FROM app.prompt_versions WHERE prompt_version = 'C-s0-g1'").fetchone()
    assert text == f"{SYSTEM}\n{HINT}"
