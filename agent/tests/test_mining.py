"""Failure mining per SPEC 9.4 with a mocked Jev (P2-06)."""

from datetime import datetime, timezone

from conftest import FakeJev
from scar_schemas.models import (
    ChatMessage,
    FailureCause,
    MineRequest,
    Origin,
    RunResult,
    Split,
    TaskFamily,
    ToolCall,
)

from scar_agent.jev import TRIAGE_QUESTIONS, Triage
from scar_agent.mining import apply_cap, build_state, exclusion_reason, mine, to_record

REQ = MineRequest(arm="D", seed=0, generation=1, model_version="base", prompt_version="system_v1")


def make_run(run_id, task_id="task_1", passed=False, stop_reason="submitted", purpose="traffic",
             origin=Origin.generated, generation=1, messages=None):
    return RunResult(
        run_id=run_id, task_id=task_id, company_id="company-004", split=Split.train,
        family=TaskFamily.aggregation, model_version="base", endpoint_model_id="m",
        prompt_version="system_v1", tools_version="tools_v1", arm="D", seed=0, generation=generation,
        purpose=purpose, origin=origin, attempt_index=0, temperature=0.0,
        messages=messages or [ChatMessage(role="system", content="s")],
        final_answer={"number": 1243.5} if stop_reason == "submitted" else None,
        stop_reason=stop_reason, passed=passed, verify_detail="number: expected 1234.50, got 1243.50",
        steps=1, input_tokens=0, output_tokens=0, latency_ms=1, sentry_trace_id=None,
        created_at=datetime.now(timezone.utc))


def test_triage_question_texts_match_spec():
    assert set(TRIAGE_QUESTIONS) == {"cause", "learnable"}
    assert set(TRIAGE_QUESTIONS["cause"].criteria) == {c.value for c in FailureCause}


def test_build_state():
    long = "x" * 900
    msgs = [
        ChatMessage(role="system", content="s"),
        ChatMessage(role="user", content="u"),
        ChatMessage(role="assistant", tool_calls=[
            ToolCall(id="a", name="get_transactions", arguments={"limit": 5}),
            ToolCall(id="b", name="sql_query", arguments={"sql": "DROP"})]),
        ChatMessage(role="tool", tool_call_id="a", name="get_transactions", content=long),
        ChatMessage(role="tool", tool_call_id="b", name="sql_query", content='{"error":"only SELECT"}'),
        ChatMessage(role="assistant", tool_calls=[ToolCall(id="c", name="submit_answer", arguments={"number": 1243.5})]),
        ChatMessage(role="tool", tool_call_id="c", name="submit_answer", content='{"ok":true}'),
    ]
    state = build_state(make_run("run_1", messages=msgs), "agg.total_by_account_period", "Q?")
    assert state["task"] == {"family": "aggregation", "template_id": "agg.total_by_account_period", "question": "Q?"}
    assert [s["tool"] for s in state["steps"]] == ["get_transactions", "sql_query"]
    assert len(state["steps"][0]["result_excerpt"]) == 500 and state["steps"][0]["was_error"] is False
    assert state["steps"][1]["was_error"] is True
    assert state["final_answer"] == {"number": 1243.5} and state["stop_reason"] == "submitted"
    assert state["verifier_detail"].startswith("number: expected")


def test_build_state_caps_steps():
    calls = [ToolCall(id=f"c{i}", name="get_accounts", arguments={}) for i in range(20)]
    run = make_run("run_1", messages=[ChatMessage(role="assistant", tool_calls=calls)])
    assert len(build_state(run, "t", "q")["steps"]) == 12


def test_exclusion_rule_order():
    assert exclusion_reason(FailureCause.reasoning_error, 0.6, "submitted") is None
    assert exclusion_reason(FailureCause.environment_error, 0.5, "timeout") == "learnable_p_below_threshold"
    assert exclusion_reason(FailureCause.ambiguous_task, 0.9, "timeout") == "cause_ambiguous_task"
    assert exclusion_reason(FailureCause.environment_error, 0.9, "submitted") == "cause_environment_error"
    assert exclusion_reason(FailureCause.gave_up, 0.9, "model_error") == "stop_reason_model_error"
    assert exclusion_reason(FailureCause.gave_up, 0.9, "max_steps") is None


def test_cap_prefers_judge_then_learnable_then_run_id():
    t = lambda p: Triage("reasoning_error", 0.9, {}, p, "jev-1.14.0")  # noqa: E731
    recs = [
        to_record(make_run("run_c"), t(0.95), REQ),
        to_record(make_run("run_b"), t(0.70), REQ),
        to_record(make_run("run_a"), t(0.95), REQ),
        to_record(make_run("run_j", origin=Origin.judge), t(0.61), REQ),
        to_record(make_run("run_x"), t(0.10), REQ),  # excluded by rule, untouched by cap
    ]
    capped = {r.run_id: r for r in apply_cap(recs, cap=3)}
    assert [rid for rid in ("run_j", "run_a", "run_c") if capped[rid].included] == ["run_j", "run_a", "run_c"]
    assert not capped["run_b"].included and capped["run_b"].exclusion_reason == "over_cap"
    assert capped["run_x"].exclusion_reason == "learnable_p_below_threshold"


def test_unknown_jev_label_maps_to_other():
    rec = to_record(make_run("r"), Triage("something_new", 0.4, {}, 0.9, "jev-1.14.0"), REQ)
    assert rec.cause == FailureCause.other


async def test_mine_selects_runs_and_is_idempotent(store):
    store.runs = [
        make_run("run_1"),
        make_run("run_2", stop_reason="timeout"),
        make_run("run_3", passed=True),                 # passed: skipped
        make_run("run_4", purpose="eval"),              # wrong purpose: skipped
        make_run("run_5", generation=2),                # other generation: skipped
        make_run("run_6", purpose="judge", origin=Origin.judge),
    ]
    jev = FakeJev()
    first = await mine(REQ, store, jev)
    assert sorted(f.run_id for f in first) == ["run_1", "run_2", "run_6"]
    assert {f.run_id: f.included for f in first} == {"run_1": True, "run_2": False, "run_6": True}
    assert len(jev.triage_calls) == 3
    second = await mine(REQ, store, jev)
    assert len(jev.triage_calls) == 3  # no new Jev calls
    assert [f.failure_id for f in second] == [f.failure_id for f in first]
