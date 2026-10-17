"""Sentry spans, tags and failure events per SPEC 8.8, captured with an in-memory transport (P2-03)."""

import asyncio
import json

import pytest
import sentry_sdk
from conftest import FakeEnv, FakeLLM, assistant, make_ctx, tool_call
from sentry_sdk.integrations.openai import OpenAIIntegration
from sentry_sdk.transport import Transport

from scar_agent import observability as obs
from scar_agent.loop import run_attempt


class Capture(Transport):
    def __init__(self, options=None):
        super().__init__(options)
        self.items = []

    def capture_envelope(self, envelope):
        for item in envelope.items:
            payload = item.payload.json
            if item.type == "span" and isinstance(payload, dict) and "items" in payload:
                self.items.extend(("span", s) for s in payload["items"])  # span v2 container
            else:
                self.items.append((item.type, payload))


@pytest.fixture
def sentry(monkeypatch):
    transport = Capture()
    sentry_sdk.init(dsn="https://public@o1.ingest.sentry.io/1", transport=transport, traces_sample_rate=1.0,
                    send_default_pii=True, disabled_integrations=[OpenAIIntegration()])
    monkeypatch.setattr(obs, "_enabled", True)
    yield transport
    sentry_sdk.get_client().close()
    sentry_sdk.init()  # back to a no-op client


def script(answer):
    return [assistant(calls=[tool_call("get_accounts", {}, "c1")]),
            assistant(calls=[tool_call("submit_answer", {"number": answer}, "c2")])]


def _attr(span, key):
    """Span attribute from either the v2 span item format or the legacy `data` dict."""
    attrs = span.get("attributes") or {}
    if key in attrs:
        v = attrs[key]
        return v.get("value") if isinstance(v, dict) else v
    return (span.get("data") or {}).get(key)


async def test_run_trace_shape_and_isolation(sentry):
    env = FakeEnv(expected_number=100.0)
    ok = make_ctx(FakeLLM(script(100.0)), env, seed=1)
    bad = make_ctx(FakeLLM(script(7.0)), env, seed=2)
    r_ok, r_bad = await asyncio.gather(run_attempt(ok, 0), run_attempt(bad, 0))
    sentry_sdk.flush()

    assert r_ok.sentry_trace_id and r_bad.sentry_trace_id and r_ok.sentry_trace_id != r_bad.sentry_trace_id
    txs = [p for t, p in sentry.items if t == "transaction"]
    assert {t["transaction"] for t in txs} == {"invoke_agent ledger-agent"}
    by_run = {t["tags"]["run_id"]: t for t in txs}
    assert by_run[r_ok.run_id]["tags"]["seed"] == "1" and by_run[r_bad.run_id]["tags"]["seed"] == "2"
    assert by_run[r_ok.run_id]["tags"]["passed"] == "true"
    assert by_run[r_bad.run_id]["tags"]["passed"] == "false"
    for key in ("task_id", "arm", "generation", "purpose", "origin", "split", "family", "template_id",
                "model_version", "prompt_version", "stop_reason"):
        assert key in by_run[r_ok.run_id]["tags"]
    assert by_run[r_ok.run_id]["contexts"]["trace"]["op"] == "gen_ai.invoke_agent"

    spans = [p for t, p in sentry.items if t == "span"]
    spans += [s for t in txs for s in t.get("spans", [])]
    names = sorted({s.get("name") or s.get("description") for s in spans})
    assert "chat qwen-test" in names and "execute_tool get_accounts" in names

    chat = next(s for s in spans if (s.get("name") or s.get("description")) == "chat qwen-test")
    assert _attr(chat, "gen_ai.operation.name") == "chat"
    assert _attr(chat, "gen_ai.request.model") == "qwen-test"
    assert _attr(chat, "gen_ai.usage.input_tokens") == 10
    msgs = json.loads(_attr(chat, "gen_ai.input.messages"))
    assert msgs[0]["role"] == "system" and msgs[0]["parts"][0]["type"] == "text"

    tool = next(s for s in spans if (s.get("name") or s.get("description")) == "execute_tool get_accounts")
    assert _attr(tool, "gen_ai.tool.name") == "get_accounts"
    assert _attr(tool, "gen_ai.tool.call.arguments") == "{}"
    assert "Rent" in _attr(tool, "gen_ai.tool.call.result")

    events = [p for t, p in sentry.items if t == "event"]
    assert len(events) == 1  # only the failed traffic run
    ev = events[0]
    assert ev["message"] == "agent_run_failed" or ev.get("logentry", {}).get("message") == "agent_run_failed"
    assert ev["level"] == "warning"
    assert ev["fingerprint"] == ["agent_run_failed", "agg.total_by_account_period", "submitted"]
    assert ev["tags"]["run_id"] == r_bad.run_id
    assert ev["contexts"]["trace"]["trace_id"] == r_bad.sentry_trace_id


async def test_no_failure_event_for_eval_runs(sentry):
    await run_attempt(make_ctx(FakeLLM(script(7.0)), FakeEnv(), purpose="eval"), 0)
    sentry_sdk.flush()
    assert not [p for t, p in sentry.items if t == "event"]


def test_to_sentry_messages():
    out = obs.to_sentry_messages([
        {"role": "assistant", "content": "hi", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "sql_query", "arguments": '{"sql":"SELECT 1"}'}},
            {"id": "c2", "type": "function", "function": {"name": "sql_query", "arguments": "{bad"}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "sql_query", "content": "[]"},
    ])
    assert out[0]["parts"] == [
        {"type": "text", "content": "hi"},
        {"type": "tool_call", "id": "c1", "name": "sql_query", "arguments": {"sql": "SELECT 1"}},
        {"type": "tool_call", "id": "c2", "name": "sql_query", "arguments": "{bad"}]
    assert out[1] == {"role": "tool", "parts": [
        {"type": "tool_call_response", "id": "c1", "name": "sql_query", "content": "[]"}]}


def test_trace_url(monkeypatch):
    monkeypatch.setenv("SENTRY_ORG", "scar")
    assert obs.trace_url("abc") == "https://scar.sentry.io/explore/traces/trace/abc/"
    assert obs.trace_url(None) is None
