import json
from datetime import datetime, timezone

import httpx
import pytest
from scar_schemas.constants import EVAL_SET_SIZE
from scar_schemas.models import GenerationRecord

from scar_train.campaign import (
    Campaign,
    CampaignError,
    Services,
    Target,
    campaign_lock,
    demo_target,
    experiment_plan,
    main,
)

from .factories import model_version


def record(target, status="promoted", **fields):
    return GenerationRecord(
        generation_id=target.generation_id,
        arm=target.arm,
        seed=target.seed,
        generation=target.generation,
        status=status,
        parent_model_version="base",
        parent_prompt_version="system_v1",
        candidate_model_version=target.generation_id if target.arm == "D" else None,
        candidate_prompt_version=None,
        current_model_version=target.generation_id if status == "promoted" and target.arm == "D" else "base",
        current_prompt_version="system_v1",
        parent_eval_score=0.4,
        eval_score=0.5,
        started_at=datetime.now(timezone.utc),
        **fields,
    ).model_dump(mode="json")


class Server:
    def __init__(self):
        self.records = {}
        self.jobs = {}
        self.posts = []
        self.failed_jobs = 0
        self.busy_job = None
        self.drop_next_status = False
        self.outages = 0

    def __call__(self, request):
        path = request.url.path
        if self.outages:
            self.outages -= 1
            return httpx.Response(503, json={"error": "unavailable"})
        if path == "/health":
            return httpx.Response(200, json={"ok": True})
        if path == "/models":
            return httpx.Response(200, json=[model_version().model_dump(mode="json")])
        if path.startswith("/eval_sets/"):
            return httpx.Response(
                200,
                json={
                    "eval_set_id": "eval_v1",
                    "task_ids": [f"task_{index}" for index in range(EVAL_SET_SIZE)],
                    "sha256": "x",
                },
            )
        if path.startswith("/generations/"):
            body = self.records.get(path.rsplit("/", 1)[1])
            return httpx.Response(200, json=body) if body else httpx.Response(404, json={"error": "not_found"})
        if path == "/loop/run":
            body = json.loads(request.content)
            target = Target(body["arm"], body["seed"], body["generations"][0])
            self.posts.append(target)
            if self.busy_job:
                job_id, self.busy_job = self.busy_job, None
                self.jobs[job_id] = target
                return httpx.Response(
                    409, json={"error": "lineage_busy", "detail": f"job {job_id} is already running lineage D-s0"}
                )
            job_id = f"job_{len(self.posts)}"
            self.jobs[job_id] = target
            return httpx.Response(200, json={"job_id": job_id})
        if path.startswith("/loop/status/"):
            job_id = path.rsplit("/", 1)[1]
            if self.drop_next_status:
                self.drop_next_status = False
                return httpx.Response(404, json={"error": "not_found"})
            target = self.jobs[job_id]
            if self.failed_jobs:
                self.failed_jobs -= 1
                self.records[target.generation_id] = record(target, "failed")
                status = "failed"
            else:
                self.records[target.generation_id] = record(target)
                status = "done"
            return httpx.Response(
                200, json={"status": status, "current_generation_id": target.generation_id, "log_tail": [status]}
            )
        raise AssertionError((request.method, request.url))


def driver(server, tmp_path, **kwargs):
    http = httpx.Client(transport=httpx.MockTransport(server))
    services = Services("http://train", "http://env", "http://agent", http=http)
    return Campaign(services, tmp_path, sleep=lambda seconds: None, echo=lambda line: None, **kwargs)


def test_run_order_and_minimum_milestone():
    plan = experiment_plan()
    assert plan[0] == Target("A", 0, 0)
    assert [(target.arm, target.seed) for target in plan[1::4]] == [
        ("D", 0),
        ("C", 0),
        ("B", 0),
        ("D", 1),
        ("D", 2),
        ("C", 1),
        ("C", 2),
    ]
    assert len(plan) == len(set(plan)) == 29
    assert len(experiment_plan(include_b_seeds=True)) == 37
    assert [target.generation_id for target in experiment_plan(minimum=True)] == [
        "A-s0-g0",
        "D-s0-g1",
        "D-s0-g2",
        "C-s0-g1",
        "B-s0-g1",
    ]


def test_serial_campaign_records_evidence_and_skips_completed_generations(tmp_path):
    server = Server()
    campaign = driver(server, tmp_path)
    campaign.services.preflight()
    plan = experiment_plan(minimum=True)
    campaign.run(plan)
    assert server.posts == plan
    assert json.loads((tmp_path / "campaign.json").read_text())["completed_at"]
    assert len(list(tmp_path.glob("*-s*-g*.json"))) == len(plan)
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert events[-1]["event"] == "campaign_finished"
    assert len([event for event in events if event["event"] == "job_attached"]) == len(plan)
    driver(server, tmp_path).run(plan)
    assert server.posts == plan


def test_failed_generation_retries_then_resumes(tmp_path):
    server = Server()
    server.failed_jobs = 1
    target = Target("D", 0, 1)
    driver(server, tmp_path).run([target])
    assert server.posts == [target, target]
    assert json.loads((tmp_path / "campaign.json").read_text())["failures"] == {target.generation_id: 1}


def test_retry_limit_persists_across_driver_restarts(tmp_path):
    server = Server()
    server.failed_jobs = 20
    target = Target("D", 0, 1)
    for _ in range(2):
        with pytest.raises(CampaignError, match="failed 2 times|exhausted retries"):
            driver(server, tmp_path, retries=1).run([target])
    assert server.posts == [target, target]


@pytest.mark.parametrize("mode", ["busy", "lost", "transport"])
def test_reconnects_without_changing_generation(tmp_path, mode):
    server = Server()
    if mode == "busy":
        server.busy_job = "job_existing"
    elif mode == "lost":
        server.drop_next_status = True
    else:
        server.outages = 1
    target = Target("D", 0, 1)
    driver(server, tmp_path).run([target])
    assert server.records[target.generation_id]["status"] == "promoted"
    assert all(post == target for post in server.posts)


def test_interrupt_preserves_job_id_for_next_driver(tmp_path):
    server = Server()
    target = Target("D", 0, 1)
    campaign = driver(server, tmp_path)
    original = campaign.request

    def interrupt(method, path, **kwargs):
        if path.startswith("/loop/status/"):
            raise KeyboardInterrupt
        return original(method, path, **kwargs)

    campaign.request = interrupt
    with pytest.raises(KeyboardInterrupt):
        campaign.run([target])
    assert json.loads((tmp_path / "campaign.json").read_text())["jobs"][target.generation_id] == "job_1"
    driver(server, tmp_path).run([target])
    assert server.posts == [target]


def test_rejects_fake_and_tiny_results_and_changed_plans(tmp_path):
    server = Server()
    target = Target("D", 0, 1)
    for notes in ("[tiny]", "fake trainer: candidate serves base"):
        server.records[target.generation_id] = record(target, notes=notes)
        with pytest.raises(CampaignError, match="tiny or fake"):
            driver(server, tmp_path).run([target])
    server.records.clear()
    driver(server, tmp_path).run([target])
    with pytest.raises(CampaignError, match="different plan"):
        driver(server, tmp_path).run([Target("C", 0, 1)])


def test_demo_resumes_unfinished_generation_then_uses_next_generation():
    finished = GenerationRecord.model_validate(record(Target("D", 0, 2), "rejected"))
    unfinished = GenerationRecord.model_validate(record(Target("D", 0, 3), "failed"))
    assert demo_target([]) == Target("D", 0, 1)
    assert demo_target([finished]) == Target("D", 0, 3)
    assert demo_target([unfinished, finished]) == Target("D", 0, 3)


def test_driver_lock_and_deadline(tmp_path):
    with campaign_lock(tmp_path), pytest.raises(CampaignError, match="another campaign"):
        with campaign_lock(tmp_path):
            pass
    server = Server()
    with pytest.raises(CampaignError, match="time limit"):
        driver(server, tmp_path, max_hours=0).run([Target("D", 0, 1)])
    assert not server.posts


def test_preflight_rejects_an_unfrozen_eval_set(tmp_path):
    server = Server()

    def short_set(request):
        if request.url.path.startswith("/eval_sets/"):
            return httpx.Response(200, json={"eval_set_id": "eval_v1", "task_ids": ["task_1"], "sha256": "x"})
        return server(request)

    with pytest.raises(CampaignError, match="300 distinct"):
        driver(short_set, tmp_path).services.preflight()


def test_lost_post_response_recovers_the_existing_job(tmp_path):
    server = Server()
    interrupted = False

    def handler(request):
        nonlocal interrupted
        if request.url.path == "/loop/run" and not interrupted:
            response = server(request)
            server.busy_job = response.json()["job_id"]
            interrupted = True
            raise httpx.ReadTimeout("lost response", request=request)
        return server(request)

    target = Target("D", 0, 1)
    driver(handler, tmp_path).run([target])
    assert list(server.jobs) == ["job_1"]
    assert json.loads((tmp_path / "campaign.json").read_text())["jobs"] == {}


def test_done_job_without_finished_generation_stops_the_campaign(tmp_path):
    server = Server()

    def handler(request):
        if request.url.path.startswith("/loop/status/"):
            return httpx.Response(200, json={"status": "done", "current_generation_id": None, "log_tail": []})
        return server(request)

    with pytest.raises(CampaignError, match="without completing D-s0-g1"):
        driver(handler, tmp_path).run([Target("D", 0, 1), Target("D", 0, 2)])
    assert server.posts == [Target("D", 0, 1)]


def test_plan_only_never_calls_services(monkeypatch, capsys):
    def unexpected(*args, **kwargs):
        raise AssertionError("plan-only must not contact services")

    monkeypatch.setattr(Services, "call", unexpected)
    assert main(["--plan-only"]) == 0
    assert "D-s0-g4" in capsys.readouterr().out


def test_persistent_service_failure_has_a_bounded_retry_budget(tmp_path):
    server = Server()
    server.outages = 20
    with pytest.raises(CampaignError, match="HTTP 503"):
        driver(server, tmp_path, retries=2).run([Target("D", 0, 1)])
    assert server.outages == 17 and not server.posts
