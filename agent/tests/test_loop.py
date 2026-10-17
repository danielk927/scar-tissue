"""Agent loop behavior per SPEC 9.2, with a mocked model and env (P2-02)."""

import asyncio
import json

import httpx
import openai
import pytest
from conftest import SECRET_EXPECTED, FakeEnv, FakeLLM, assistant, make_ctx, tool_call
from scar_schemas.constants import MAX_TOKENS_PER_TURN, TOOL_RESULT_MAX_CHARS
from scar_schemas.openai_format import from_openai_message, load_tools, to_openai_messages

from scar_agent import loop as loop_mod
from scar_agent.loop import NUDGE, RAW_ARGS_ERROR, run_attempt


async def run(script, env=None, **req):
    env = env or FakeEnv()
    llm = FakeLLM(script)
    result = await run_attempt(make_ctx(llm, env, **req), 0)
    return result, llm, env


async def test_submitted_happy_path():
    result, llm, env = await run([
        assistant(calls=[tool_call("get_accounts", {}, "c1")]),
        assistant(calls=[tool_call("submit_answer", {"number": 100.0}, "c2")]),
    ])
    assert result.stop_reason == "submitted"
    assert result.passed and result.final_answer == {"number": 100.0}
    assert result.steps == 2 and result.input_tokens == 20 and result.output_tokens == 10
    roles = [m.role for m in result.messages]
    assert roles == ["system", "user", "assistant", "tool", "assistant", "tool"]
    assert result.messages[1].content == "Company: Stitchform Apparel Co\n\nWhat was total spend in Rent from 2026-01-01 to 2026-03-31?"
    assert result.messages[3].content == '[{"account_id":"a1","name":"Rent"}]'  # deterministic JSON
    assert result.messages[3].name == "get_accounts" and result.messages[3].tool_call_id == "c1"
    assert result.messages[5].content == '{"ok":true}'
    assert env.tool_calls[0].company_id == "company-004" and env.tool_calls[0].name == "get_accounts"
    assert env.verifies[0].stop_reason == "submitted"


async def test_model_call_parameters():
    _, llm, _ = await run([assistant(calls=[tool_call("submit_answer", {"number": 1})])], temperature=0.8)
    kw = llm.requests[0]
    assert kw["model"] == "qwen-test" and kw["tool_choice"] == "auto"
    assert kw["tools"] == load_tools("tools_v1")
    assert kw["temperature"] == 0.8 and kw["max_tokens"] == MAX_TOKENS_PER_TURN


async def test_expected_answer_never_sent_to_model():
    _, llm, _ = await run([
        assistant(calls=[tool_call("get_accounts", {})]),
        assistant(calls=[tool_call("submit_answer", {"number": 1})]),
    ])
    sent = json.dumps([r["messages"] for r in llm.requests])
    assert str(SECRET_EXPECTED) not in sent and "expected" not in sent


async def test_nudge_then_submit():
    result, _, _ = await run([
        assistant("Let me think."),
        assistant(calls=[tool_call("submit_answer", {"number": 100.0})]),
    ])
    assert result.stop_reason == "submitted"
    assert result.messages[3].role == "user" and result.messages[3].content == NUDGE


async def test_no_submit_after_second_text_turn():
    result, _, _ = await run([assistant("a"), assistant(calls=[tool_call("get_accounts", {})]), assistant("b")])
    assert result.stop_reason == "no_submit"
    assert not result.passed and result.verify_detail == "not submitted: no_submit"


async def test_max_steps():
    script = [assistant(calls=[tool_call("get_accounts", {}, f"c{i}")]) for i in range(5)]
    result, llm, _ = await run(script, max_steps=3)
    assert result.stop_reason == "max_steps" and result.steps == 3 and len(llm.requests) == 3


async def test_model_error():
    err = openai.APIConnectionError(request=httpx.Request("POST", "http://model"))
    result, _, _ = await run([assistant(calls=[tool_call("get_accounts", {})]), err])
    assert result.stop_reason == "model_error" and result.steps == 2


async def test_malformed_model_response_is_model_error():
    result, _, _ = await run([{"role": "bogus"}])
    assert result.stop_reason == "model_error"


async def test_tool_error_on_env_503():
    result, _, _ = await run([assistant(calls=[tool_call("get_accounts", {})])], env=FakeEnv(infra_fail=True))
    assert result.stop_reason == "tool_error"
    assert result.messages[-1].role == "assistant"  # no tool message appended


async def test_tool_ok_false_returns_error_to_model():
    env = FakeEnv(tool_ok=False, tool_error="unknown account_id")
    result, _, _ = await run([
        assistant(calls=[tool_call("get_transactions", {"account_id": "x"})]),
        assistant(calls=[tool_call("submit_answer", {"number": 100.0})]),
    ], env=env)
    assert result.messages[3].content == '{"error":"unknown account_id"}'


async def test_malformed_arguments_not_executed():
    result, _, env = await run([
        assistant(calls=[tool_call("get_transactions", "{not json")]),
        assistant(calls=[tool_call("submit_answer", {"number": 100.0})]),
    ])
    assert env.tool_calls == []
    assert result.messages[2].tool_calls[0].arguments == {"__raw__": "{not json"}
    assert result.messages[3].content == RAW_ARGS_ERROR


async def test_truncation():
    env = FakeEnv(tool_result=[{"row": "x" * 100} for _ in range(200)])
    result, _, _ = await run([
        assistant(calls=[tool_call("get_transactions", {})]),
        assistant(calls=[tool_call("submit_answer", {"number": 100.0})]),
    ], env=env)
    content = result.messages[3].content
    assert content.endswith("...[truncated]")
    assert len(content) == TOOL_RESULT_MAX_CHARS + len("...[truncated]")


async def test_submit_ignores_later_calls_in_same_message():
    result, _, env = await run([assistant(calls=[
        tool_call("submit_answer", {"number": 100.0}, "c1"),
        tool_call("get_accounts", {}, "c2"),
    ])])
    assert result.stop_reason == "submitted" and env.tool_calls == []
    assert [m.role for m in result.messages] == ["system", "user", "assistant", "tool"]


async def test_multiple_tool_calls_in_order():
    result, _, env = await run([
        assistant(calls=[tool_call("get_accounts", {}, "c1"), tool_call("get_vendors", {}, "c2")]),
        assistant(calls=[tool_call("submit_answer", {"number": 100.0}, "c3")]),
    ])
    assert [c.name for c in env.tool_calls] == ["get_accounts", "get_vendors"]
    assert [m.tool_call_id for m in result.messages if m.role == "tool"] == ["c1", "c2", "c3"]


async def test_whole_run_timeout(monkeypatch):
    monkeypatch.setattr(loop_mod, "RUN_TIMEOUT_S", 0.05)

    async def slow():
        await asyncio.sleep(1)

    result, _, env = await run([assistant(calls=[tool_call("get_accounts", {})]), slow])
    assert result.stop_reason == "timeout"
    assert env.verifies[0].stop_reason == "timeout"
    assert [m.role for m in result.messages] == ["system", "user", "assistant", "tool"]


@pytest.mark.parametrize("msg", [
    {"role": "assistant", "content": "hello"},
    {"role": "assistant", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "get_transactions",
                                                      "arguments": '{"limit":5,"start_date":"2026-01-01"}'}}]},
    {"role": "assistant", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "sql_query", "arguments": "{oops"}}]},
    {"role": "tool", "tool_call_id": "c1", "name": "get_accounts", "content": "[]"},
    {"role": "user", "content": "Company: X\n\nQ"},
])
def test_openai_format_round_trip(msg):
    assert to_openai_messages([from_openai_message(msg)])[0] == msg
