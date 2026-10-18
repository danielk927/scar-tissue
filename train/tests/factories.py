"""Rows for the Person 3 tables, built with the shared models."""

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import psycopg
from psycopg.types.json import Jsonb
from scar_schemas.models import (
    ChatMessage,
    ExpectedAnswer,
    FailureRecord,
    GenerationRecord,
    GenStatus,
    ModelVersion,
    RunBatchRequest,
    RunRequest,
    RunResult,
    Task,
    ToolCall,
)

from scar_train.clients import AgentClient

NOW = datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc)


def model_version(version: str = "base", status: str = "promoted", **kw) -> ModelVersion:
    fields = dict(
        version=version,
        arm=None,
        seed=None,
        generation=0,
        parent_version=None,
        base_model_hf_id="Qwen/Example-4B",
        endpoint_base_url="http://models.local/v1",
        endpoint_model_id="base",
        adapter_uri=None,
        training_job_id=None,
        snapshot_id=None,
        status=status,
        created_at=NOW,
    )
    fields.update(kw)
    return ModelVersion(**fields)


def generation(arm: str, seed: int, g: int, status: GenStatus, current_model: str, current_prompt: str = "system_v1"):
    return GenerationRecord(
        generation_id=f"{arm}-s{seed}-g{g}",
        arm=arm,
        seed=seed,
        generation=g,
        status=status,
        parent_model_version="base",
        parent_prompt_version="system_v1",
        candidate_model_version=None,
        candidate_prompt_version=None,
        current_model_version=current_model,
        current_prompt_version=current_prompt,
        started_at=NOW,
    )


def task(task_id: str, split: str = "train") -> Task:
    return Task(
        task_id=task_id,
        company_id="company-001",
        split=split,
        family="aggregation",
        template_id="agg.total_by_account_period",
        params={},
        prompt=f"Question {task_id}",
        expected=ExpectedAnswer(answer_type="number", number=Decimal("1234.50")),
        difficulty=1,
        origin="generated",
        created_at=NOW,
    )


def insert_task(conn: psycopg.Connection, t: Task) -> None:
    """What the env service stores per task (app.tasks, Person 1's table; tests only)."""
    conn.execute(
        "INSERT INTO app.tasks (task_id, company_id, split, family, template_id, difficulty, origin, parent_failure_id,"
        " body, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            t.task_id,
            t.company_id,
            t.split.value,
            t.family.value,
            t.template_id,
            t.difficulty,
            t.origin.value,
            t.parent_failure_id,
            Jsonb(t.model_dump(mode="json")),
            t.created_at,
        ),
    )


def transcript(task_id: str, calls: list[tuple[str, dict]], answer: dict | None) -> list[ChatMessage]:
    """system, user, one assistant tool call + tool result per call, then submit_answer (if answer is not None)."""
    msgs = [
        ChatMessage(role="system", content="You are a careful bookkeeping assistant."),
        ChatMessage(role="user", content=f"Company: Acme Café\n\nQuestion {task_id}"),
    ]
    for i, (name, args) in enumerate(calls):
        msgs.append(ChatMessage(role="assistant", tool_calls=[ToolCall(id=f"call_{i}", name=name, arguments=args)]))
        msgs.append(ChatMessage(role="tool", tool_call_id=f"call_{i}", name=name, content='{"rows":[]}'))
    if answer is not None:
        call = ToolCall(id="call_submit", name="submit_answer", arguments=answer)
        msgs.append(ChatMessage(role="assistant", tool_calls=[call]))
        msgs.append(ChatMessage(role="tool", tool_call_id="call_submit", name="submit_answer", content='{"ok":true}'))
    return msgs


def run_result(
    run_id: str,
    task_id: str,
    *,
    passed: bool = False,
    calls: list[tuple[str, dict]] | None = None,
    answer: dict | None = None,
    steps: int | None = None,
    stop_reason: str = "submitted",
    **kw,
) -> RunResult:
    """A RunResult. Defaults: arm D, seed 0, generation 1, base + system_v1, purpose sampling, train split; the
    transcript is built from `calls` and `answer` unless `messages` is given."""
    calls = [("get_accounts", {})] if calls is None else calls
    if answer is None and stop_reason == "submitted":
        answer = {"number": 1234.5}
    fields = dict(
        run_id=run_id,
        task_id=task_id,
        company_id="company-001",
        split="train",
        family="aggregation",
        model_version="base",
        endpoint_model_id="base",
        prompt_version="system_v1",
        tools_version="tools_v1",
        arm="D",
        seed=0,
        generation=1,
        purpose="sampling",
        origin="variant",
        attempt_index=0,
        temperature=0.8,
        messages=transcript(task_id, calls, answer),
        final_answer=answer,
        stop_reason=stop_reason,
        passed=passed,
        verify_detail="ok" if passed else "number: expected 1234.50, got 1.00",
        steps=len(calls) + 1 if steps is None else steps,
        input_tokens=100,
        output_tokens=20,
        latency_ms=500,
        sentry_trace_id=None,
        created_at=NOW,
    )
    fields.update(kw)
    return RunResult(**fields)


def insert_run(conn: psycopg.Connection, r: RunResult) -> None:
    """What the agent service stores per run (app.runs, Person 2's table; tests only)."""
    conn.execute(
        "INSERT INTO app.runs (run_id, task_id, model_version, prompt_version, arm, seed, generation, purpose, origin,"
        " split, family, passed, stop_reason, body, created_at) VALUES"
        " (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            r.run_id,
            r.task_id,
            r.model_version,
            r.prompt_version,
            r.arm,
            r.seed,
            r.generation,
            r.purpose,
            r.origin.value,
            r.split.value,
            r.family.value,
            r.passed,
            r.stop_reason,
            Jsonb(r.model_dump(mode="json")),
            r.created_at,
        ),
    )


class FakeAgent(AgentClient):
    """Stands in for agent /agent/run_batch: answers each attempt with run_result(**outcome(request, attempt_index))
    and stores it in app.runs, like the real agent. Run IDs and created_at follow the app.runs row count
    (run_fake_0001, ...), so they are unique across fakes in one test and ordering never depends on random IDs."""

    def __init__(self, conn: psycopg.Connection, outcome: Callable[[RunRequest, int], dict] = lambda req, i: {}):
        super().__init__("http://agent.test")
        self.conn, self.outcome = conn, outcome
        self.batches: list[RunBatchRequest] = []

    @property
    def requests(self) -> list[RunRequest]:
        return [r for b in self.batches for r in b.requests]

    def run_batch(self, req: RunBatchRequest) -> list[RunResult]:
        self.batches.append(req)
        runs = []
        for r in req.requests:
            for i in range(r.n):
                n = self.conn.execute("SELECT count(*) FROM app.runs").fetchone()[0] + 1
                fields = dict(
                    model_version=r.model_version,
                    prompt_version=r.prompt_version,
                    arm=r.arm,
                    seed=r.seed,
                    generation=r.generation,
                    purpose=r.purpose,
                    temperature=r.temperature,
                    attempt_index=i,
                    created_at=NOW + timedelta(hours=1, seconds=n),
                )
                fields.update(self.outcome(r, i))
                run = run_result(f"run_fake_{n:04d}", r.task_id, **fields)
                insert_run(self.conn, run)
                runs.append(run)
        return runs


def failure(failure_id: str, run_id: str, task_id: str, *, included: bool = True, **kw) -> FailureRecord:
    """A FailureRecord as agent /failures/mine returns it. Defaults: arm D, seed 0, generation 1, base model."""
    fields = dict(
        failure_id=failure_id,
        run_id=run_id,
        task_id=task_id,
        arm="D",
        seed=0,
        generation=1,
        model_version="base",
        origin="generated",
        cause="reasoning_error",
        cause_confidence=0.9,
        cause_probabilities={"reasoning_error": 0.9},
        learnable_p=0.9,
        included=included,
        exclusion_reason=None if included else "learnable_p_below_threshold",
        jev_model="jev-1.14.0",
        created_at=NOW,
    )
    fields.update(kw)
    return FailureRecord(**fields)
