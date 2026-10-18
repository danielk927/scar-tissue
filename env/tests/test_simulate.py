"""Traffic simulator (SPEC 9.5, P1-11): POST /simulate against a fake agent service."""

import json
import random
from collections.abc import Callable
from datetime import timedelta

import httpx
import pytest
from scar_schemas.constants import TRAIN_POOL_SIZE
from scar_schemas.models import Origin

from scar_env.routes.loop import get_agent_client
from scar_env.simulate import MAX_CONCURRENCY, batch_timeout
from scar_env.store import get_task, store_task
from scar_env.tasks import make_task

from .test_mutate import NOW, insert_run, run_result

REQ = {"arm": "D", "seed": 1, "generation": 2, "model_version": "base", "prompt_version": "system_v1", "n_tasks": 20}
RENT_Q1 = {"account_id": "company-901:acct-rent", "start_date": "2026-01-01", "end_date": "2026-03-31"}


def pool_task_id(position: int) -> str:
    return f"task_pool{position:04d}"


def scheduled(seed: int, generation: int, n: int) -> list[str]:
    """SPEC 9.5's schedule, written out independently of scar_env.simulate."""
    return [pool_task_id(p) for p in random.Random(1000 * seed + generation).sample(range(TRAIN_POOL_SIZE), n)]


def passes(task_id: str) -> bool:
    return int(task_id[-4:]) % 3 == 0


def fill_pool(conn, size: int) -> None:
    """Pool position p holds task_pool{p:04d}, a copy of one train task."""
    base = make_task(
        conn,
        "agg.total_by_account_period",
        "company-901",
        RENT_Q1,
        origin=Origin.generated,
        difficulty=1,
        rng=random.Random(0),
    )
    with conn.transaction():
        for p in range(size):
            store_task(conn, base.model_copy(update={"task_id": pool_task_id(p)}))
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO app.train_pool (position, task_id) VALUES (%s, %s)",
                [(p, pool_task_id(p)) for p in range(size)],
            )


@pytest.fixture
def pool(conn, ledger) -> None:
    fill_pool(conn, TRAIN_POOL_SIZE)


class FakeAgent:
    """Stands in for agent POST /agent/run_batch. Like the real agent, it stores one run per RunRequest in app.runs as
    it goes and returns them in request order; a task passes when its pool position is divisible by 3.
    `fail_after = k` stores k runs and then answers 500 (a batch that died midway); `respond` replaces the handler."""

    def __init__(self, conn):
        self.conn = conn
        self.requests: list[httpx.Request] = []
        self.fail_after: int | None = None
        self.respond: Callable[[httpx.Request], httpx.Response] | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.respond:
            return self.respond(request)
        runs = []
        for r in json.loads(request.content)["requests"]:
            if len(runs) == self.fail_after:
                return httpx.Response(500, text="Internal Server Error")
            run = run_result(
                get_task(self.conn, r["task_id"]),
                **{k: r[k] for k in ("model_version", "prompt_version", "arm", "seed", "generation", "purpose")},
                passed=passes(r["task_id"]),
            )
            insert_run(self.conn, run)
            runs.append(run)
        return httpx.Response(200, json=[run.model_dump(mode="json") for run in runs])

    def sent_task_ids(self, i: int) -> list[str]:
        return [r["task_id"] for r in json.loads(self.requests[i].content)["requests"]]


@pytest.fixture
def agent(client, conn) -> FakeAgent:
    fake = FakeAgent(conn)

    async def fake_client():
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handler), base_url="http://agent") as c:
            yield c

    client.app.dependency_overrides[get_agent_client] = fake_client
    return fake


def simulate(client, **overrides):
    return client.post("/simulate", json=REQ | overrides)


def stored_run_ids(conn) -> dict[str, str]:
    return dict(conn.execute("SELECT task_id, run_id FROM app.runs ORDER BY task_id").fetchall())


def test_arms_with_the_same_seed_and_generation_get_the_same_pool_positions(client, agent, pool):
    assert simulate(client, arm="D").status_code == 200
    assert simulate(client, arm="A").status_code == 200
    assert agent.sent_task_ids(0) == agent.sent_task_ids(1) == scheduled(1, 2, 20)
    assert simulate(client, arm="D", generation=3).status_code == 200
    assert agent.sent_task_ids(2) == scheduled(1, 3, 20) != scheduled(1, 2, 20)


def test_the_agent_gets_one_traffic_batch_in_sampled_order(client, agent, pool):
    assert simulate(client).status_code == 200
    (request,) = agent.requests
    assert (request.method, request.url.path) == ("POST", "/agent/run_batch")
    assert json.loads(request.content) == {
        "requests": [
            {
                "task_id": task_id,
                "model_version": "base",
                "prompt_version": "system_v1",
                "arm": "D",
                "seed": 1,
                "generation": 2,
                "purpose": "traffic",
                "temperature": 0.0,
                "n": 1,
                "max_steps": 12,
            }
            for task_id in scheduled(1, 2, 20)
        ],
        "max_concurrency": MAX_CONCURRENCY,
    }
    assert request.extensions["timeout"]["read"] == batch_timeout(20) == 3 * 180
    assert batch_timeout(200) == 14 * 180  # 200 runs in waves of 16, each wave allowed the 180 s run timeout


def test_the_result_counts_every_sampled_run_in_order(client, conn, agent, pool):
    resp = simulate(client, n_tasks=30)
    assert resp.status_code == 200
    body, expected = resp.json(), scheduled(1, 2, 30)
    run_ids = stored_run_ids(conn)
    assert body["run_ids"] == [run_ids[t] for t in expected]
    passed = sum(passes(t) for t in expected)
    assert 0 < passed < 30 and body["passed"] == passed and body["failed"] == 30 - passed


@pytest.mark.parametrize("size", [0, 10])
def test_an_incomplete_pool_is_a_409(client, conn, ledger, agent, size):
    fill_pool(conn, size)
    resp = simulate(client)
    assert resp.status_code == 409
    assert resp.json()["error"] == "train_pool_incomplete" and f"{size} of {TRAIN_POOL_SIZE}" in resp.json()["detail"]
    assert agent.requests == []


def raise_connect_error(request):
    raise httpx.ConnectError("connection refused", request=request)


def raise_read_timeout(request):
    raise httpx.ReadTimeout("timed out", request=request)


@pytest.mark.parametrize(
    "respond",
    [
        raise_connect_error,
        raise_read_timeout,
        lambda _: httpx.Response(500, text="Internal Server Error"),
        lambda _: httpx.Response(503, json={"error": "dependency_unavailable", "detail": "model endpoint down"}),
    ],
)
def test_an_unavailable_or_failing_agent_is_a_503(client, agent, pool, respond):
    agent.respond = respond
    resp = simulate(client)
    assert resp.status_code == 503 and resp.json()["error"] == "agent_unavailable"


def test_an_agent_404_passes_through(client, agent, pool):
    agent.respond = lambda _: httpx.Response(404, json={"error": "not_found", "detail": "unknown model_version nope"})
    resp = simulate(client, model_version="nope")
    assert resp.status_code == 404 and resp.json()["error"] == "not_found"
    assert "unknown model_version nope" in resp.json()["detail"]


def test_a_failed_batch_resumes_with_only_the_missing_tasks(client, conn, agent, pool):
    expected = scheduled(1, 2, 20)
    agent.fail_after = 7
    assert simulate(client).status_code == 503
    first_runs = stored_run_ids(conn)
    assert set(first_runs) == set(expected[:7])

    agent.fail_after = None
    resp = simulate(client)
    assert resp.status_code == 200
    assert agent.sent_task_ids(1) == expected[7:]
    assert resp.json()["run_ids"] == [stored_run_ids(conn)[t] for t in expected]
    assert resp.json()["run_ids"][:7] == [first_runs[t] for t in expected[:7]]

    assert simulate(client).json() == resp.json()  # nothing missing: the agent is not called again
    assert len(agent.requests) == 2


def test_only_traffic_runs_with_the_same_key_are_reused(client, conn, agent, pool):
    expected = scheduled(1, 2, 20)
    key = {k: REQ[k] for k in ("arm", "seed", "generation", "model_version", "prompt_version")}
    task = [get_task(conn, t) for t in expected[:7]]
    reused = run_result(task[0], **key, passed=True, created_at=NOW)
    for run in [
        reused,
        run_result(task[0], **key, created_at=NOW + timedelta(seconds=1)),  # a later duplicate: the earliest wins
        run_result(task[1], **key | {"arm": "A"}),
        run_result(task[2], **key | {"seed": 0}),
        run_result(task[3], **key | {"generation": 1}),
        run_result(task[4], **key | {"model_version": "dev"}),
        run_result(task[5], **key | {"prompt_version": "B-s1-g1"}),
        run_result(task[6], **key, purpose="eval"),
    ]:
        insert_run(conn, run)
    resp = simulate(client)
    assert resp.status_code == 200
    assert agent.sent_task_ids(0) == expected[1:]
    assert resp.json()["run_ids"][0] == reused.run_id
    assert resp.json()["passed"] == 1 + sum(passes(t) for t in expected[1:])


@pytest.mark.parametrize("n_tasks", [0, TRAIN_POOL_SIZE + 1])
def test_n_tasks_must_fit_the_pool(client, agent, pool, n_tasks):
    resp = simulate(client, n_tasks=n_tasks)
    assert resp.status_code == 422 and resp.json()["error"] == "invalid_n_tasks"
    assert agent.requests == []
