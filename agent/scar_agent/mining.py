"""Failure mining with Jev (SPEC 9.4): state builder, inclusion rule, cap and order, idempotent mine."""

import asyncio
import json
from datetime import datetime, timezone

from scar_schemas.constants import FAILURE_CAP_PER_GEN, INCLUDE_LEARNABLE_P
from scar_schemas.ids import new_id
from scar_schemas.models import FailureCause, FailureRecord, MineRequest, Origin, RunResult

from .clients import Unavailable
from .db import Store
from .jev import Jev, Triage

MAX_STATE_STEPS = 12
RESULT_EXCERPT_CHARS = 500
JEV_CONCURRENCY = 8

EXCLUDED_CAUSES = {FailureCause.environment_error, FailureCause.ambiguous_task}
EXCLUDED_STOP_REASONS = {"model_error", "tool_error", "timeout"}


def _was_error(content: str | None) -> bool:
    try:
        parsed = json.loads(content or "")
    except json.JSONDecodeError:
        return False
    return isinstance(parsed, dict) and "error" in parsed


def build_state(run: RunResult, template_id: str, question: str) -> dict:
    """The object sent to Jev. Never includes the expected answer (only the verifier's detail line)."""
    tool_results = {m.tool_call_id: m.content for m in run.messages if m.role == "tool"}
    steps = []
    for m in run.messages:
        if m.role != "assistant" or not m.tool_calls:
            continue
        for call in m.tool_calls:
            if call.name == "submit_answer":  # covered by final_answer
                continue
            content = tool_results.get(call.id)
            steps.append({
                "tool": call.name,
                "arguments": call.arguments,
                "result_excerpt": (content or "")[:RESULT_EXCERPT_CHARS],
                "was_error": _was_error(content),
            })
    return {
        "task": {"family": run.family.value, "template_id": template_id, "question": question},
        "steps": steps[:MAX_STATE_STEPS],
        "final_answer": run.final_answer,
        "stop_reason": run.stop_reason,
        "verifier_detail": run.verify_detail,
    }


def exclusion_reason(cause: FailureCause, learnable_p: float, stop_reason: str) -> str | None:
    """First rule that fails, in the order SPEC 9.4 lists them; None means included."""
    if learnable_p < INCLUDE_LEARNABLE_P:
        return "learnable_p_below_threshold"
    if cause in EXCLUDED_CAUSES:
        return f"cause_{cause.value}"
    if stop_reason in EXCLUDED_STOP_REASONS:
        return f"stop_reason_{stop_reason}"
    return None


def apply_cap(records: list[FailureRecord], cap: int = FAILURE_CAP_PER_GEN) -> list[FailureRecord]:
    """Judge failures first, then highest learnable_p, ties by run_id. Beyond the cap -> over_cap."""
    included = sorted(
        (r for r in records if r.included),
        key=lambda r: (r.origin != Origin.judge, -r.learnable_p, r.run_id),
    )
    over = {r.failure_id for r in included[cap:]}
    return [
        r.model_copy(update={"included": False, "exclusion_reason": "over_cap"})
        if r.failure_id in over else r
        for r in records
    ]


def to_record(run: RunResult, t: Triage, req: MineRequest) -> FailureRecord:
    try:
        cause = FailureCause(t.cause)
    except ValueError:
        cause = FailureCause.other
    reason = exclusion_reason(cause, t.learnable_p, run.stop_reason)
    return FailureRecord(
        failure_id=new_id("fail"),
        run_id=run.run_id,
        task_id=run.task_id,
        arm=req.arm,
        seed=req.seed,
        generation=req.generation,
        model_version=run.model_version,
        origin=run.origin,
        cause=cause,
        cause_confidence=t.cause_confidence,
        cause_probabilities=t.cause_probabilities,
        learnable_p=t.learnable_p,
        included=reason is None,
        exclusion_reason=reason,
        jev_model=t.jev_model,
        created_at=datetime.now(timezone.utc),
    )


async def mine(req: MineRequest, store: Store, jev: Jev) -> list[FailureRecord]:
    existing = await store.list_failures(arm=req.arm, seed=req.seed, generation=req.generation)
    if existing:
        return existing  # idempotent: no new Jev calls

    runs = await store.failed_runs_to_mine(req.arm, req.seed, req.generation)
    if not runs:
        return []
    meta = await store.get_task_meta(sorted({r.task_id for r in runs}))
    sem = asyncio.Semaphore(JEV_CONCURRENCY)

    async def triage_one(run: RunResult) -> FailureRecord:
        template_id, question = meta[run.task_id]
        async with sem:
            try:
                t = await jev.triage(build_state(run, template_id, question))
            except Exception as e:  # TypeSafeError after retries; nothing is stored
                raise Unavailable(f"jev triage failed for {run.run_id}: {e!r}") from e
        return to_record(run, t, req)

    records = apply_cap(list(await asyncio.gather(*(triage_one(r) for r in runs))))
    await store.insert_failures(records)
    return await store.list_failures(arm=req.arm, seed=req.seed, generation=req.generation)
