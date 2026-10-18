import json

import httpx
import pytest

from scar_train import register_base as rb
from scar_train import registry
from scar_train.checks import USER_MESSAGE, ToolCallCheckError, tool_call_check

URL = "http://models.local/v1"


def completion(message: dict) -> dict:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "m",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", **message}}],
    }


def tool_message(arguments: str) -> dict:
    call = {"id": "call_1", "type": "function", "function": {"name": "get_accounts", "arguments": arguments}}
    return {"content": None, "tool_calls": [call]}


def fake_server(message: dict, seen: list | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, json=completion(message))

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_tool_call_check_sends_the_agent_request_shape():
    seen = []
    tool_call_check(URL, "base-model", "secret", http_client=fake_server(tool_message("{}"), seen))
    req = seen[0]
    body = json.loads(req.content)
    assert str(req.url) == f"{URL}/chat/completions"
    assert req.headers["authorization"] == "Bearer secret"
    assert body["model"] == "base-model" and body["tool_choice"] == "auto" and body["temperature"] == 0
    assert body["messages"][-1] == {"role": "user", "content": USER_MESSAGE}
    assert [t["function"]["name"] for t in body["tools"]][0] == "get_accounts"


@pytest.mark.parametrize(
    ("message", "match"),
    [
        ({"content": "Here are the accounts."}, "no tool call"),
        (tool_message("{not json"), "unparseable arguments"),
    ],
)
def test_tool_call_check_rejects_answers_without_a_parseable_tool_call(message, match):
    with pytest.raises(ToolCallCheckError, match=match):
        tool_call_check(URL, "m", None, http_client=fake_server(message))


def test_tool_call_check_reports_endpoint_errors():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404, json={"error": "no model"})))
    with pytest.raises(ToolCallCheckError, match="models.local"):
        tool_call_check(URL, "m", None, http_client=client)


def test_register_base_checks_then_registers_once(conn):
    calls = []

    def check(url, model_id, key):
        calls.append((url, model_id, key))

    kw = dict(endpoint_base_url=URL, endpoint_model_id="m", base_model_hf_id="Qwen/X", api_key="k", check=check)
    mv = rb.register_base(conn, **kw)
    assert (mv.version, mv.generation, mv.status, mv.arm, mv.parent_version) == ("base", 0, "promoted", None, None)
    assert registry.get_model_version(conn, "base") == mv
    assert rb.register_base(conn, **kw) == mv  # identical: re-checked, not re-registered
    assert calls == [(URL, "m", "k")] * 2
    with pytest.raises(rb.RegisterError, match="refusing to change"):
        rb.register_base(conn, **{**kw, "endpoint_model_id": "other"})
    assert len(calls) == 2


def test_register_base_failed_check_registers_nothing(conn):
    def check(url, model_id, key):
        raise ToolCallCheckError("no tool call")

    with pytest.raises(ToolCallCheckError):
        rb.register_base(
            conn, endpoint_base_url=URL, endpoint_model_id="m", base_model_hf_id="Qwen/X", api_key=None, check=check
        )
    assert registry.get_model_version(conn, "base") is None


def test_main_uses_model_api_key_and_base_model_hf_id(db_url, conn, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(rb, "tool_call_check", lambda *a: calls.append(a))
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("MODEL_API_KEY", "k")
    monkeypatch.delenv("BASE_MODEL_HF_ID", raising=False)
    with pytest.raises(SystemExit):
        rb.main(["--endpoint-base-url", URL, "--endpoint-model-id", "m"])
    monkeypatch.setenv("BASE_MODEL_HF_ID", "Qwen/X")
    assert rb.main(["--endpoint-base-url", URL, "--endpoint-model-id", "m"]) == 0
    assert calls == [(URL, "m", "k")]
    assert registry.get_model_version(conn, "base").base_model_hf_id == "Qwen/X"
    assert rb.main(["--endpoint-base-url", "http://elsewhere/v1", "--endpoint-model-id", "m"]) == 1
    assert "refusing to change" in capsys.readouterr().err
