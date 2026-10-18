import json
import threading
import time

import httpx
import pytest
from scar_schemas.models import RunBatchRequest, RunRequest, SimulateRequest

from scar_train.clients import AgentClient, DependencyError, EnvClient, batch_timeout
from scar_train.trainer import FakeDeployer, FakeTrainer

from .factories import model_version


def test_batch_timeout():
    assert batch_timeout(200, 16) == (13 + 1) * 180
    assert batch_timeout(0, 8) == 2 * 180


def test_agent_client_posts_shared_models_and_maps_errors():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json=[])

    agent = AgentClient("http://agent", transport=httpx.MockTransport(handler))
    req = RunBatchRequest(requests=[RunRequest(task_id="task_1", model_version="base", purpose="eval")])
    assert agent.run_batch(req) == []
    assert seen[0][0] == "/agent/run_batch" and seen[0][1]["requests"][0]["purpose"] == "eval"

    failing = EnvClient("http://env", transport=httpx.MockTransport(lambda r: httpx.Response(503, json={})))
    with pytest.raises(DependencyError, match="HTTP 503"):
        failing.simulate(SimulateRequest(arm="D", seed=0, generation=1, model_version="base", prompt_version="v"))


def test_agent_batches_take_turns_on_the_shared_replica():
    in_flight, peak, guard = 0, 0, threading.Lock()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        with guard:
            in_flight += 1
            peak = max(peak, in_flight)
        time.sleep(0.05)
        with guard:
            in_flight -= 1
        return httpx.Response(200, json=[]) if request.url.path == "/agent/run_batch" else httpx.Response(503)

    transport = httpx.MockTransport(handler)
    req = RunBatchRequest(requests=[RunRequest(task_id="task_1", model_version="base", purpose="eval")])
    sim = SimulateRequest(arm="B", seed=0, generation=1, model_version="base", prompt_version="v")

    def simulate():
        with pytest.raises(DependencyError):
            EnvClient("http://env", transport=transport).simulate(sim)

    calls = [lambda: AgentClient("http://agent", transport=transport).run_batch(req)] * 2 + [simulate]
    threads = [threading.Thread(target=call) for call in calls]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak == 1


def test_fake_backends_serve_the_base_endpoint(tmp_path):
    data = tmp_path / "d.jsonl"
    data.write_text("{}\n")
    adapter = FakeTrainer().train(version="D-s0-g1", dataset_path=data, n_examples=1, base_model_hf_id="x", seed=0)
    dep = FakeDeployer().deploy(version="D-s0-g1", adapter=adapter, base=model_version())
    assert (dep.endpoint_base_url, dep.endpoint_model_id) == ("http://models.local/v1", "base")
    with pytest.raises(FileNotFoundError):
        FakeTrainer().train(version="v", dataset_path=tmp_path / "missing", n_examples=0, base_model_hf_id="x", seed=0)
