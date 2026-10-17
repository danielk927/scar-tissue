"""Synthetic rows for the tables the learn-steps read, and fakes for the reflection LM and the agent and env clients.
No test calls a real LM or service."""

from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal

import psycopg
from psycopg.types.json import Jsonb
from scar_schemas.ids import new_id
from scar_schemas.models import (
    ChatMessage,
    ExpectedAnswer,
    FailureRecord,
    MutateRequest,
    RunBatchRequest,
    RunResult,
    Task,
    ToolCall,
)

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
SYSTEM_V1 = "You are a careful bookkeeping assistant.\n\nFinish by calling submit_answer exactly once."


def make_task(
    *, split: str = "train", origin: str = "generated", parent_failure_id: str | None = None, prompt: str | None = None
) -> Task:
    task_id = new_id("task")
    return Task(
        task_id=task_id,
        company_id="company-901" if split == "train" else "company-903",
        split=split,
        family="aggregation",
        template_id="agg.total_by_account_period",
        params={"account_id": "company-901:acct-rent", "start_date": "2026-01-01", "end_date": "2026-03-31"},
        prompt=prompt or f"How much was spent on rent from 2026-01-01 to 2026-03-31? ({task_id[-6:]})",
        expected=ExpectedAnswer(answer_type="number", number=Decimal("7500.00")),
        difficulty=1,
        origin=origin,
        parent_failure_id=parent_failure_id,
        created_at=NOW,
    )


def insert_task(conn: psycopg.Connection, task: Task) -> Task:
    conn.execute(
        "INSERT INTO app.tasks (task_id, company_id, split, family, template_id, difficulty, origin,"
        " parent_failure_id, body, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            task.task_id,
            task.company_id,
            task.split.value,
            task.family.value,
            task.template_id,
            task.difficulty,
            task.origin.value,
            task.parent_failure_id,
            Jsonb(task.model_dump(mode="json")),
            task.created_at,
        ),
    )
    return task


def insert_pool(conn: psycopg.Connection, n: int) -> list[Task]:
    tasks = [insert_task(conn, make_task()) for _ in range(n)]
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO app.train_pool VALUES (%s, %s)", [(i, t.task_id) for i, t in enumerate(tasks)])
    return tasks


def make_run(
    task: Task,
    *,
    passed: bool,
    prompt_version: str = "system_v1",
    arm: str = "B",
    seed: int = 0,
    generation: int = 1,
    purpose: str = "traffic",
    temperature: float = 0.0,
) -> RunResult:
    answer = 7500 if passed else 7600
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
        seed=seed,
        generation=generation,
        purpose=purpose,
        origin=task.origin,
        attempt_index=0,
        temperature=temperature,
        messages=[
            ChatMessage(role="system", content="system prompt"),
            ChatMessage(role="user", content=f"Company: Alpha Supply Co\n\n{task.prompt}"),
            ChatMessage(
                role="assistant",
                tool_calls=[ToolCall(id="c1", name="get_transactions", arguments={"account_id": "company-901:rent"})],
            ),
            ChatMessage(role="tool", tool_call_id="c1", name="get_transactions", content='[{"amount":"2500.00"}]'),
            ChatMessage(
                role="assistant", tool_calls=[ToolCall(id="c2", name="submit_answer", arguments={"number": answer})]
            ),
            ChatMessage(role="tool", tool_call_id="c2", name="submit_answer", content='{"ok":true}'),
        ],
        final_answer={"number": answer},
        stop_reason="submitted",
        passed=passed,
        verify_detail=f"number: expected 7500.00, got {answer}",
        steps=2,
        input_tokens=100,
        output_tokens=20,
        latency_ms=1000,
        sentry_trace_id=None,
        created_at=NOW,
    )


def add_failure(
    conn: psycopg.Connection,
    *,
    arm: str = "B",
    seed: int = 0,
    generation: int = 1,
    split: str = "train",
    included: bool = True,
    cause: str = "bad_tool_args",
) -> FailureRecord:
    """Insert a failed traffic run with its task and its FailureRecord."""
    task = insert_task(conn, make_task(split=split))
    run = make_run(task, passed=False, arm=arm, seed=seed, generation=generation)
    conn.execute(
        "INSERT INTO app.runs (run_id, task_id, model_version, prompt_version, arm, seed, generation, purpose, origin,"
        " split, family, passed, stop_reason, body, created_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            run.run_id,
            run.task_id,
            run.model_version,
            run.prompt_version,
            run.arm,
            run.seed,
            run.generation,
            run.purpose,
            run.origin.value,
            run.split.value,
            run.family.value,
            run.passed,
            run.stop_reason,
            Jsonb(run.model_dump(mode="json")),
            run.created_at,
        ),
    )
    failure = FailureRecord(
        failure_id=new_id("fail"),
        run_id=run.run_id,
        task_id=task.task_id,
        arm=arm,
        seed=seed,
        generation=generation,
        model_version="base",
        origin=task.origin,
        cause=cause,
        cause_confidence=0.8,
        cause_probabilities={cause: 0.8, "other": 0.2},
        learnable_p=0.9 if included else 0.2,
        included=included,
        exclusion_reason=None if included else "not_learnable",
        jev_model="jev-1.14.0",
        created_at=NOW,
    )
    conn.execute(
        "INSERT INTO app.failures (failure_id, run_id, arm, seed, generation, included, body, created_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (failure.failure_id, run.run_id, arm, seed, generation, included, Jsonb(failure.model_dump(mode="json")), NOW),
    )
    return failure


class FakeLM:
    """Reflection LM fake: `reply(prompt) -> str`; records every prompt."""

    def __init__(self, reply: Callable[[str], str]):
        self.reply = reply
        self.prompts: list[str] = []

    def __call__(self, prompt: str | list[dict]) -> str:
        assert isinstance(prompt, str)
        self.prompts.append(prompt)
        return self.reply(prompt)


class FakeAgent:
    """Agent client fake: a run passes when `passes(prompt_text, task)`. The prompt version must already be stored,
    as the real agent loads it from app.prompt_versions."""

    def __init__(self, conn: psycopg.Connection, passes: Callable[[str, Task], bool]):
        self.conn = conn
        self.passes = passes
        self.batches: list[RunBatchRequest] = []

    @property
    def requests(self):
        return [r for b in self.batches for r in b.requests]

    def run_batch(self, req: RunBatchRequest) -> list[RunResult]:
        self.batches.append(req)
        runs = []
        for r in req.requests:
            (text,) = self.conn.execute(
                "SELECT body->>'text' FROM app.prompt_versions WHERE prompt_version = %s", (r.prompt_version,)
            ).fetchone()
            (body,) = self.conn.execute("SELECT body FROM app.tasks WHERE task_id = %s", (r.task_id,)).fetchone()
            task = Task.model_validate(body)
            runs.append(
                make_run(
                    task,
                    passed=self.passes(text, task),
                    prompt_version=r.prompt_version,
                    arm=r.arm,
                    seed=r.seed,
                    generation=r.generation,
                    purpose=r.purpose,
                    temperature=r.temperature,
                )
            )
        return runs


class FakeEnv:
    """Env client fake: /mutate stores `variants_per_failure` variants per failure, like the real mutator."""

    def __init__(self, conn: psycopg.Connection):
        self.conn = conn
        self.calls: list[MutateRequest] = []

    def mutate(self, req: MutateRequest) -> list[Task]:
        self.calls.append(req)
        return [
            insert_task(self.conn, make_task(origin="variant", parent_failure_id=fid))
            for fid in req.failure_ids
            for _ in range(req.variants_per_failure)
        ]
