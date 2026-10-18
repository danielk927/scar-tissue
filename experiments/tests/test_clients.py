import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from scar_schemas.models import MutateRequest, RunBatchRequest, RunRequest

from scar_experiments import clients
from scar_experiments.clients import AgentClient, EnvClient, openai_reflection_lm
from scar_experiments.errors import DependencyError

from .factories import make_run, make_task


def _fake_post(monkeypatch, status=200, body=None, error=None):
    calls = []

    def post(url, json, timeout):
        calls.append((url, json, timeout))
        if error is not None:
            raise error
        return httpx.Response(status, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(clients.httpx, "post", post)
    return calls


def test_agent_client_posts_run_batch_and_parses_runs(monkeypatch):
    task = make_task()
    run = make_run(task, passed=True, purpose="gepa")
    calls = _fake_post(monkeypatch, body=[run.model_dump(mode="json")])
    req = RunBatchRequest(requests=[RunRequest(task_id=task.task_id, model_version="base", purpose="gepa")] * 9)
    assert AgentClient("http://agent:8002/").run_batch(req) == [run]
    ((url, body, timeout),) = calls
    assert url == "http://agent:8002/agent/run_batch" and body == req.model_dump(mode="json")
    assert timeout == 60 + 180 * 2  # 9 attempts at max_concurrency 8: two waves of whole runs


@pytest.mark.parametrize(
    "status, body, error",
    [
        (503, {"error": "dependency_unavailable", "detail": "db down"}, None),
        (404, {"error": "not_found", "detail": "unknown prompt_version C-cand-x"}, None),
        (None, None, httpx.ConnectError("connection refused")),
    ],
)
def test_dependency_failures_raise_dependency_error(monkeypatch, status, body, error):
    _fake_post(monkeypatch, status, body, error)
    with pytest.raises(DependencyError, match="POST http://env:8001/mutate"):
        EnvClient("http://env:8001").mutate(MutateRequest(failure_ids=["fail_1"], seed=0))


@pytest.fixture
def chat_server():
    """A local OpenAI-compatible /v1/chat/completions that records requests and replies per `state`."""
    state = {"requests": [], "status": 200}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["requests"].append((self.path, self.headers["Authorization"], body))
            if state["status"] == 200:
                reply = {
                    "id": "c1",
                    "object": "chat.completion",
                    "created": 0,
                    "model": body["model"],
                    "choices": [
                        {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}
                    ],
                }
            else:
                reply = {"error": {"message": "model not found", "type": "invalid_request_error"}}
            data = json.dumps(reply).encode()
            self.send_response(state["status"])
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1", state
    server.shutdown()
    server.server_close()


def test_openai_reflection_lm_calls_chat_completions_at_temperature_zero(chat_server):
    base_url, state = chat_server
    lm = openai_reflection_lm(base_url, "secret-key", "strong-model")
    assert lm("Write one lesson.") == "ok"
    assert lm([{"role": "user", "content": "Improve this prompt."}]) == "ok"
    (path, auth, body), (_, _, body2) = state["requests"]
    assert path == "/v1/chat/completions" and auth == "Bearer secret-key"
    assert body["model"] == "strong-model" and body["temperature"] == 0
    assert body["messages"] == [{"role": "user", "content": "Write one lesson."}]
    assert body2["messages"] == [{"role": "user", "content": "Improve this prompt."}]


def test_openai_reflection_lm_errors_raise_dependency_error(chat_server):
    base_url, state = chat_server
    state["status"] = 400  # not retried by the openai client, so the test stays fast
    with pytest.raises(DependencyError, match="reflection LM call failed"):
        openai_reflection_lm(base_url, "secret-key", "missing-model")("hello")
