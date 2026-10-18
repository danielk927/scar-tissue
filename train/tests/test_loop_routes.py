import threading
import time

import pytest

from scar_train import registry
from scar_train.routes.loop import get_loop_context

from .factories import model_version
from .test_loop import Fakes, context


def wait(client, job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/loop/status/{job_id}").json()
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish")


@pytest.fixture
def f():
    return Fakes()


@pytest.fixture
def loop_client(client, conn, f, tmp_path):
    registry.put_model_version(conn, model_version())
    client.app.dependency_overrides[get_loop_context] = lambda: context(f, tmp_path)
    return client


def post(client, arm="D", seed=0, generations=(1,), **kw):
    return client.post("/loop/run", json={"arm": arm, "seed": seed, "generations": list(generations), **kw})


def test_a_job_runs_its_generations_in_order(loop_client):
    r = post(loop_client, generations=[1, 2])
    assert r.status_code == 200
    body = wait(loop_client, r.json()["job_id"])
    assert (body["status"], body["current_generation_id"]) == ("done", "D-s0-g2")
    tail = "\n".join(body["log_tail"])
    assert tail.index("D-s0-g1 collecting") < tail.index("D-s0-g2 collecting") < tail.index("D-s0-g2 promoted")
    gens = loop_client.get("/generations", params={"arm": "D", "seed": 0}).json()
    assert [(g["generation_id"], g["status"]) for g in gens] == [
        ("D-s0-g0", "promoted"),
        ("D-s0-g1", "promoted"),
        ("D-s0-g2", "promoted"),
    ]


def test_one_job_per_lineage(loop_client, f):
    entered, release = threading.Event(), threading.Event()
    simulate = f.simulate

    def blocking_simulate(req):
        if req.seed == 0:
            entered.set()
            assert release.wait(10)
        return simulate(req)

    f.simulate = blocking_simulate
    first = post(loop_client).json()["job_id"]
    assert entered.wait(10)
    running = loop_client.get(f"/loop/status/{first}").json()
    assert (running["status"], running["current_generation_id"]) == ("running", "D-s0-g1")
    r = post(loop_client, generations=[2])
    assert r.status_code == 409 and r.json()["error"] == "lineage_busy" and first in r.json()["detail"]
    other = post(loop_client, seed=1)
    assert other.status_code == 200
    release.set()
    assert wait(loop_client, first)["status"] == "done"
    assert wait(loop_client, other.json()["job_id"])["status"] == "done"
    again = wait(loop_client, post(loop_client).json()["job_id"])  # finished generations are skipped
    assert again["status"] == "done" and any("D-s0-g1 already promoted" in line for line in again["log_tail"])


def test_a_failed_job_stops_and_a_rerun_resumes(loop_client, f):
    f.fail["mine"] = RuntimeError("agent down")
    body = wait(loop_client, post(loop_client, generations=[1, 2]).json()["job_id"])
    assert (body["status"], body["current_generation_id"]) == ("failed", "D-s0-g1")
    assert "D-s0-g1 failed at mining: RuntimeError: agent down" in "\n".join(body["log_tail"])
    assert body["log_tail"][-1].endswith("job failed: RuntimeError: agent down")
    assert loop_client.get("/generations/D-s0-g1").json()["status"] == "failed"

    f.calls.clear()
    body = wait(loop_client, post(loop_client, generations=[1, 2]).json()["job_id"])
    assert body["status"] == "done" and f.calls[0] == "mine"


@pytest.mark.parametrize(
    ("payload", "status", "error"),
    [
        ({"arm": "E", "seed": 0, "generations": [1]}, 422, "not_implemented"),
        ({"arm": "A", "seed": 0, "generations": [1]}, 422, "invalid_generations"),
        ({"arm": "D", "seed": 0, "generations": [2]}, 409, "previous_generation_unfinished"),
        ({"arm": "Z", "seed": 0, "generations": [1]}, 422, "invalid_input"),
    ],
)
def test_run_refusals(loop_client, payload, status, error):
    r = loop_client.post("/loop/run", json=payload)
    assert (r.status_code, r.json()["error"]) == (status, error)
    if payload["arm"] == "E":
        assert "SPEC D-11: first to cut" in r.json()["detail"]


def test_tiny_and_full_jobs_never_mix(loop_client):
    assert wait(loop_client, post(loop_client, tiny=True).json()["job_id"])["status"] == "done"
    r = post(loop_client, generations=[2])
    assert (r.status_code, r.json()["error"]) == (409, "mode_conflict")
    assert loop_client.get("/generations/D-s0-g1").json()["notes"].startswith("[tiny]")


def test_unknown_job_and_unavailable_trainer(client, conn, monkeypatch):
    assert client.get("/loop/status/job_nope").status_code == 404
    registry.put_model_version(conn, model_version())
    monkeypatch.setenv("TRAINER", "veri")
    monkeypatch.delenv("VERI_DEPLOYMENT_ID", raising=False)  # misconfigured Veri backend
    r = post(client)
    assert (r.status_code, r.json()["error"]) == (503, "trainer_unavailable")
