"""Training-data builder (SPEC 9.7, P3-07).

Lineage `{arm}-s{seed}` (arm D, or E), generation g:
- build_snapshot samples the current model on `task_ids` (this generation's variants plus the original failed
  tasks), keeps passed attempts, adds replay examples, asserts train-only, and writes TrainingExample rows under a
  new snapshot_id. Idempotent per (lineage, generation): a second call returns the existing snapshot.
- lineage_examples is the cumulative training set for the lineage (all its snapshots so far), deduplicated by
  origin_run_id, in a deterministic order.
- export_jsonl writes one line per example: {"messages": to_openai_messages(ex.messages), "tools": load_tools()}
  (SPEC 9.8); messages are verbatim from the passed run (R7).

Details of build_snapshot:
- Sample: one RunBatchRequest (purpose "sampling", BASE_PROMPT_VERSION, `temperature`, n = missing attempts per task).
- Resume: sampling runs already stored for the same (arm, seed, generation, model_version, prompt_version, task) are
  reused (the earliest k_samples per task); runs that ended in model_error, tool_error or timeout are not attempts.
  Only the missing attempts are requested, so a crashed step never resamples. A batch lost entirely to those
  infrastructure failures raises DependencyError.
- Keep: passed attempts only, per task at most MAX_EXAMPLES_PER_TASK, fewest steps first, ties by run_id; an attempt
  with the same (tool name, dumps_args(arguments)) sequence and dumps_args(final_answer) as one already kept for the
  task is dropped.
- Zero-pass tasks are logged and returned in zero_pass_task_ids (no teacher path). If nothing passes, nothing is
  written and snapshot_id is None.
- Replay: round(REPLAY_RATIO x n_self) passed `traffic` runs of the same arm and seed from generations <= g, train
  split, BASE_PROMPT_VERSION, one run per task (the earliest), tasks not among this snapshot's self examples, runs not
  already used by the lineage; sampled with random.Random(seed * 100 + generation) from the task_id-sorted list.
- A snapshot belongs to generation g when its self examples come from sampling runs of generation g.
"""

import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb
from scar_schemas.constants import (
    BASE_PROMPT_VERSION,
    K_SAMPLES,
    MAX_EXAMPLES_PER_TASK,
    REPLAY_RATIO,
    SAMPLE_TEMPERATURE,
)
from scar_schemas.guards import assert_train_only
from scar_schemas.ids import new_id
from scar_schemas.models import RunBatchRequest, RunRequest, RunResult, TrainingExample
from scar_schemas.openai_format import dumps_args, load_tools, to_openai_messages

from . import registry
from .clients import AgentClient, DependencyError
from .log import get_logger

MAX_CONCURRENCY = 16  # agent attempts in flight per batch
INFRA_STOP_REASONS = ("model_error", "tool_error", "timeout")  # not attempts: the model never got a fair try

log = get_logger(__name__)


@dataclass(frozen=True)
class Snapshot:
    snapshot_id: str | None  # None when nothing was kept
    n_self: int
    n_replay: int
    zero_pass_task_ids: list[str] = field(default_factory=list)


def run_requests(agent: AgentClient, requests: list[RunRequest]) -> list[RunResult]:
    """One agent /agent/run_batch at MAX_CONCURRENCY. A batch lost entirely to infrastructure failures is an outage:
    DependencyError, so the step fails and a retry resumes from the stored runs."""
    runs = agent.run_batch(RunBatchRequest(requests=requests, max_concurrency=MAX_CONCURRENCY))
    if all(r.stop_reason in INFRA_STOP_REASONS for r in runs):
        raise DependencyError(f"agent /agent/run_batch: all {len(runs)} runs ended in {'/'.join(INFRA_STOP_REASONS)}")
    return runs


def build_snapshot(
    conn: psycopg.Connection,
    agent: AgentClient,
    *,
    arm: str,
    seed: int,
    generation: int,
    model_version: str,
    task_ids: list[str],
    k_samples: int = K_SAMPLES,
    temperature: float = SAMPLE_TEMPERATURE,
) -> Snapshot:
    lin = registry.lineage(arm, seed)
    ctx = {"arm": arm, "seed": seed, "generation": generation, "model_version": model_version}
    task_ids = list(dict.fromkeys(task_ids))
    assert_train_only(task_ids, conn)  # fail before spending agent runs on a leaked task
    attempts = _stored_attempts(conn, arm, seed, generation, model_version, task_ids, k_samples)
    existing = _existing_snapshot(conn, lin, generation)

    if existing is None:
        missing = {t: k_samples - len(a) for t, a in attempts.items() if len(a) < k_samples}
        log.info(
            "build_snapshot: %d of %d attempts already stored, requesting %d",
            len(task_ids) * k_samples - sum(missing.values()),
            len(task_ids) * k_samples,
            sum(missing.values()),
            extra=ctx,
        )
        if missing:
            requests = [
                RunRequest(
                    task_id=t,
                    model_version=model_version,
                    prompt_version=BASE_PROMPT_VERSION,
                    arm=arm,
                    seed=seed,
                    generation=generation,
                    purpose="sampling",
                    temperature=temperature,
                    n=n,
                )
                for t, n in missing.items()
            ]
            for run in run_requests(agent, requests):
                if run.stop_reason not in INFRA_STOP_REASONS:
                    attempts[run.task_id].append(run)

    zero_pass = [t for t in task_ids if not any(r.passed for r in attempts[t])]
    if zero_pass:
        log.info("build_snapshot: %d zero-pass tasks: %s", len(zero_pass), ", ".join(zero_pass), extra=ctx)
    if existing is not None:
        counts = dict(
            conn.execute(
                "SELECT source, count(*) FROM app.training_examples WHERE snapshot_id = %s GROUP BY source",
                (existing,),
            ).fetchall()
        )
        log.info("build_snapshot: snapshot %s already exists for this generation", existing, extra=ctx)
        return Snapshot(existing, counts.get("self", 0), counts.get("replay", 0), zero_pass)

    self_runs = [r for t in task_ids for r in _keep(attempts[t])]
    if not self_runs:
        return Snapshot(None, 0, 0, zero_pass)
    replay_runs = _replay(
        conn,
        arm=arm,
        seed=seed,
        generation=generation,
        lin=lin,
        exclude_task_ids=sorted({r.task_id for r in self_runs}),
        n=round(REPLAY_RATIO * len(self_runs)),
    )
    assert_train_only(sorted({r.task_id for r in self_runs + replay_runs}), conn)

    snapshot_id, now = new_id("snap"), datetime.now(timezone.utc)
    examples = [_example(r, snapshot_id, lin, "self", now) for r in self_runs]
    examples += [_example(r, snapshot_id, lin, "replay", now) for r in replay_runs]
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO app.training_examples (example_id, snapshot_id, lineage, task_id, source, body, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [
                (
                    ex.example_id,
                    ex.snapshot_id,
                    ex.lineage,
                    ex.task_id,
                    ex.source,
                    Jsonb(ex.model_dump(mode="json")),
                    now,
                )
                for ex in examples
            ],
        )
    log.info(
        "build_snapshot: wrote snapshot %s: %d self, %d replay",
        snapshot_id,
        len(self_runs),
        len(replay_runs),
        extra=ctx,
    )
    return Snapshot(snapshot_id, len(self_runs), len(replay_runs), zero_pass)


def _stored_attempts(
    conn: psycopg.Connection,
    arm: str,
    seed: int,
    generation: int,
    model_version: str,
    task_ids: list[str],
    k_samples: int,
) -> dict[str, list[RunResult]]:
    """task_id -> the earliest k_samples stored sampling attempts with this key (infrastructure failures skipped)."""
    rows = conn.execute(
        "SELECT body FROM app.runs WHERE purpose = 'sampling' AND arm = %s AND seed = %s AND generation = %s"
        " AND model_version = %s AND prompt_version = %s AND task_id = ANY(%s) AND stop_reason <> ALL(%s)"
        ' ORDER BY created_at, run_id COLLATE "C"',
        (arm, seed, generation, model_version, BASE_PROMPT_VERSION, task_ids, list(INFRA_STOP_REASONS)),
    ).fetchall()
    attempts: dict[str, list[RunResult]] = {t: [] for t in task_ids}
    for (body,) in rows:
        if len(attempts[body["task_id"]]) < k_samples:
            attempts[body["task_id"]].append(RunResult.model_validate(body))
    return attempts


def _existing_snapshot(conn: psycopg.Connection, lin: str, generation: int) -> str | None:
    row = conn.execute(
        "SELECT e.snapshot_id FROM app.training_examples e JOIN app.runs r ON r.run_id = e.body->>'origin_run_id'"
        " WHERE e.lineage = %s AND e.source = 'self' AND r.purpose = 'sampling' AND r.generation = %s"
        ' ORDER BY e.created_at, e.snapshot_id COLLATE "C" LIMIT 1',
        (lin, generation),
    ).fetchone()
    return row[0] if row else None


def _signature(run: RunResult) -> tuple:
    calls = tuple(
        (c.name, dumps_args(c.arguments)) for m in run.messages if m.role == "assistant" for c in m.tool_calls or ()
    )
    return calls, dumps_args(run.final_answer)


def _keep(attempts: list[RunResult]) -> list[RunResult]:
    """Passed attempts of one task: fewest steps first, ties by run_id, exact duplicates dropped, capped."""
    kept: list[RunResult] = []
    seen: set[tuple] = set()
    for run in sorted((r for r in attempts if r.passed), key=lambda r: (r.steps, r.run_id)):
        if len(kept) == MAX_EXAMPLES_PER_TASK:
            break
        sig = _signature(run)
        if sig not in seen:
            seen.add(sig)
            kept.append(run)
    return kept


def _replay(
    conn: psycopg.Connection,
    *,
    arm: str,
    seed: int,
    generation: int,
    lin: str,
    exclude_task_ids: list[str],
    n: int,
) -> list[RunResult]:
    rows = conn.execute(
        "SELECT DISTINCT ON (task_id) task_id, run_id FROM app.runs r WHERE purpose = 'traffic' AND passed"
        " AND split = 'train' AND arm = %s AND seed = %s AND generation <= %s AND prompt_version = %s"
        " AND task_id <> ALL(%s) AND NOT EXISTS (SELECT 1 FROM app.training_examples e"
        " WHERE e.lineage = %s AND e.body->>'origin_run_id' = r.run_id)"
        ' ORDER BY task_id, generation, created_at, run_id COLLATE "C"',
        (arm, seed, generation, BASE_PROMPT_VERSION, exclude_task_ids, lin),
    ).fetchall()
    candidates = [run_id for _, run_id in sorted(rows)]
    chosen = random.Random(seed * 100 + generation).sample(candidates, min(n, len(candidates)))
    bodies = dict(conn.execute("SELECT run_id, body FROM app.runs WHERE run_id = ANY(%s)", (chosen,)).fetchall())
    return [RunResult.model_validate(bodies[run_id]) for run_id in chosen]


def _example(run: RunResult, snapshot_id: str, lin: str, source: str, now: datetime) -> TrainingExample:
    return TrainingExample(
        example_id=new_id("ex"),
        snapshot_id=snapshot_id,
        lineage=lin,
        task_id=run.task_id,
        source=source,
        origin_run_id=run.run_id,
        prompt_version=run.prompt_version,
        tools_version=run.tools_version,
        messages=run.messages,  # verbatim (R7)
        created_at=now,
    )


def lineage_examples(conn: psycopg.Connection, lineage: str) -> list[TrainingExample]:
    rows = conn.execute(
        'SELECT body FROM app.training_examples WHERE lineage = %s ORDER BY created_at, example_id COLLATE "C"',
        (lineage,),
    ).fetchall()
    examples: list[TrainingExample] = []
    seen: set[str] = set()
    for (body,) in rows:
        ex = TrainingExample.model_validate(body)
        if ex.origin_run_id not in seen:
            seen.add(ex.origin_run_id)
            examples.append(ex)
    return examples


def export_jsonl(examples: list[TrainingExample], path: Path) -> int:
    """No sort_keys: chat templates render `tools` with tojson in key order, which must match what the agent sends."""
    tools: dict[str, list[dict]] = {}
    with path.open("w", encoding="utf-8") as f:
        for ex in examples:
            if ex.tools_version not in tools:
                tools[ex.tools_version] = load_tools(ex.tools_version)
            line = {"messages": to_openai_messages(ex.messages), "tools": tools[ex.tools_version]}
            f.write(json.dumps(line, ensure_ascii=False, separators=(",", ":")) + "\n")
    return len(examples)
