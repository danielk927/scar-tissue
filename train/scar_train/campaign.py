"""Run full-size experiments serially, resuming jobs and saving evidence for the demo."""

import argparse
import fcntl
import json
import os
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx
from pydantic import TypeAdapter
from scar_schemas.constants import EVAL_SET_ID, EVAL_SET_SIZE, N_GENERATIONS, SEEDS
from scar_schemas.models import EvalSet, GenerationRecord, ModelVersion

from .config import REPO_ROOT, get_settings

TERMINAL = ("promoted", "rejected")
RETRYABLE_HTTP = (429, 500, 502, 503, 504)


class CampaignError(RuntimeError):
    """The campaign cannot safely continue; saved jobs remain resumable."""


class RemoteError(CampaignError):
    def __init__(self, method: str, path: str, status: int, body: object):
        self.status, self.body = status, body
        super().__init__(f"{method} {path}: HTTP {status}")


@dataclass(frozen=True)
class Target:
    arm: str
    seed: int
    generation: int

    @property
    def generation_id(self) -> str:
        return f"{self.arm}-s{self.seed}-g{self.generation}"


def experiment_plan(*, minimum: bool = False, include_b_seeds: bool = False) -> list[Target]:
    if minimum:
        return [Target("A", 0, 0), Target("D", 0, 1), Target("D", 0, 2), Target("C", 0, 1), Target("B", 0, 1)]
    lineages = [("D", 0), ("C", 0), ("B", 0)]
    lineages += [(arm, seed) for arm in ("D", "C") for seed in SEEDS if seed != 0]
    if include_b_seeds:
        lineages += [("B", seed) for seed in SEEDS if seed != 0]
    return [Target("A", 0, 0)] + [
        Target(arm, seed, generation) for arm, seed in lineages for generation in range(1, N_GENERATIONS + 1)
    ]


def check_record(record: GenerationRecord) -> None:
    if record.notes.startswith("[tiny]") or "fake trainer" in record.notes.lower():
        raise CampaignError(f"{record.generation_id} contains tiny or fake results; use a clean full-size lineage")


def demo_target(records: list[GenerationRecord]) -> Target:
    records = sorted(
        (record for record in records if record.arm == "D" and record.seed == 0),
        key=lambda record: record.generation,
    )
    for record in records:
        check_record(record)
    unfinished = [record for record in records if record.status not in TERMINAL]
    if unfinished:
        return Target("D", 0, unfinished[0].generation)
    return Target("D", 0, max((record.generation for record in records), default=0) + 1)


class Services:
    def __init__(self, train_url: str, env_url: str, agent_url: str, *, http: httpx.Client):
        self.urls = {"train": train_url.rstrip("/"), "env": env_url.rstrip("/"), "agent": agent_url.rstrip("/")}
        self.http = http

    def call(self, method: str, path: str, *, service: str = "train", body: dict | None = None) -> object:
        try:
            response = self.http.request(method, self.urls[service] + path, json=body)
        except httpx.TransportError as error:
            raise CampaignError(f"{service} {method} {path}: {type(error).__name__}") from error
        try:
            payload = response.json()
        except ValueError as error:
            raise CampaignError(f"{service} {method} {path}: response is not JSON") from error
        if not response.is_success:
            raise RemoteError(method, path, response.status_code, payload)
        return payload

    def preflight(self) -> None:
        for service in self.urls:
            if self.call("GET", "/health", service=service) != {"ok": True}:
                raise CampaignError(f"{service} service is not healthy")
        models = TypeAdapter(list[ModelVersion]).validate_python(self.call("GET", "/models"))
        if not any(model.version == "base" and model.status == "promoted" for model in models):
            raise CampaignError("register the real base endpoint before running a campaign")
        eval_set = EvalSet.model_validate(self.call("GET", f"/eval_sets/{EVAL_SET_ID}", service="env"))
        if eval_set.eval_set_id != EVAL_SET_ID or len(set(eval_set.task_ids)) != EVAL_SET_SIZE:
            raise CampaignError(f"{EVAL_SET_ID} must contain exactly {EVAL_SET_SIZE} distinct frozen tasks")
        if len(eval_set.task_ids) != EVAL_SET_SIZE:
            raise CampaignError(f"{EVAL_SET_ID} contains duplicate tasks")


@contextmanager
def campaign_lock(directory: Path) -> Iterator[None]:
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "campaign.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise CampaignError(f"another campaign driver is using {directory}") from error
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def write_json(path: Path, body: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


class Campaign:
    def __init__(
        self,
        services: Services,
        directory: Path,
        *,
        poll_seconds: float = 10,
        retries: int = 3,
        max_hours: float = 8,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        echo: Callable[[str], None] = print,
    ):
        self.services, self.directory = services, directory
        self.poll_seconds, self.retries = poll_seconds, retries
        self.sleep, self.clock, self.echo = sleep, clock, echo
        self.deadline = clock() + max_hours * 3600
        self.path = directory / "campaign.json"
        self.state: dict = {}
        self.last_tail: dict[str, list[str]] = {}

    def event(self, event: str, **fields) -> None:
        body = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
        with (self.directory / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(body) + "\n")
        self.echo(json.dumps(body))

    def pause(self) -> None:
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise CampaignError("campaign time limit reached; any server job continues, rerun to reconnect")
        self.sleep(min(self.poll_seconds, remaining))

    def request(self, method: str, path: str, **kwargs) -> object:
        for attempt in range(self.retries + 1):
            if self.clock() >= self.deadline:
                raise CampaignError("campaign time limit reached; rerun to reconnect to saved jobs")
            try:
                return self.services.call(method, path, **kwargs)
            except CampaignError as error:
                if isinstance(error, RemoteError) and error.status not in RETRYABLE_HTTP:
                    raise
                if attempt == self.retries:
                    raise
                self.event("service_retry", method=method, path=path, attempt=attempt + 1, error=str(error))
                self.pause()
        raise AssertionError("unreachable")

    def record(self, target: Target) -> GenerationRecord | None:
        try:
            body = self.request("GET", f"/generations/{target.generation_id}")
        except RemoteError as error:
            if error.status == 404:
                return None
            raise
        record = GenerationRecord.model_validate(body)
        if record.generation_id != target.generation_id:
            raise CampaignError("generation endpoint returned a different generation")
        check_record(record)
        return record

    def save(self) -> None:
        write_json(self.path, self.state)

    def run(self, plan: list[Target]) -> None:
        with campaign_lock(self.directory):
            identifiers = [target.generation_id for target in plan]
            if self.path.exists():
                self.state = json.loads(self.path.read_text())
                if self.state["plan"] != identifiers or self.state["train_url"] != self.services.urls["train"]:
                    raise CampaignError("saved campaign has a different plan or train URL; choose another --output-dir")
            else:
                self.state = {"plan": identifiers, "train_url": self.services.urls["train"], "jobs": {}, "failures": {}}
                self.save()
            for target in plan:
                self.run_target(target)
            self.state["completed_at"] = datetime.now(timezone.utc).isoformat()
            self.save()
            self.event("campaign_finished", generations=len(plan))

    def run_target(self, target: Target) -> None:
        identifier = target.generation_id
        while True:
            record = self.record(target)
            job_id = self.state["jobs"].get(identifier)
            if record is not None and record.status in TERMINAL and not job_id:
                write_json(self.directory / f"{identifier}.json", record.model_dump(mode="json"))
                self.event(
                    "generation_finished", generation_id=identifier, status=record.status, eval_score=record.eval_score
                )
                return
            if job_id is None:
                if self.state["failures"].get(identifier, 0) > self.retries:
                    raise CampaignError(
                        f"{identifier} exhausted retries; inspect events.jsonl before starting a new campaign"
                    )
                try:
                    body = self.request(
                        "POST",
                        "/loop/run",
                        body={"arm": target.arm, "seed": target.seed, "generations": [target.generation]},
                    )
                    if not isinstance(body, dict) or "job_id" not in body:
                        raise CampaignError("loop endpoint returned no job ID")
                    job_id = body["job_id"]
                except RemoteError as error:
                    detail = error.body.get("detail", "") if isinstance(error.body, dict) else ""
                    code = error.body.get("error") if isinstance(error.body, dict) else None
                    if error.status != 409 or code != "lineage_busy":
                        raise
                    words = detail.split()
                    if len(words) < 2 or words[0] != "job" or not words[1].startswith("job_"):
                        raise CampaignError("lineage is busy but its job ID is unavailable") from error
                    job_id = words[1]
                if not isinstance(job_id, str) or not job_id.startswith("job_"):
                    raise CampaignError("loop endpoint returned an invalid job ID")
                self.state["jobs"][identifier] = job_id
                self.save()
                self.event("job_attached", generation_id=identifier, job_id=job_id)
            try:
                status = self.request("GET", f"/loop/status/{job_id}")
            except RemoteError as error:
                if error.status != 404:
                    raise
                self.state["jobs"].pop(identifier, None)
                self.save()
                self.event("job_lost_on_restart", generation_id=identifier, job_id=job_id)
                self.pause()
                continue
            tail = status.get("log_tail", [])
            if tail != self.last_tail.get(job_id):
                self.last_tail[job_id] = tail
                self.event("job_status", generation_id=identifier, job_id=job_id, **status)
            if status["status"] in ("done", "failed"):
                self.state["jobs"].pop(identifier, None)
                if status["status"] == "failed":
                    failures = self.state["failures"].get(identifier, 0) + 1
                    self.state["failures"][identifier] = failures
                    self.save()
                    if failures > self.retries:
                        raise CampaignError(
                            f"{identifier} failed {failures} times; inspect events.jsonl and the saved step"
                        )
                    self.event("generation_retry", generation_id=identifier, attempt=failures)
                    self.pause()
                else:
                    completed = self.record(target)
                    if completed is None or completed.status not in TERMINAL:
                        self.save()
                        raise CampaignError(f"job {job_id} finished without completing {identifier}")
                self.save()
                continue
            if status["status"] not in ("queued", "running"):
                raise CampaignError(f"job {job_id} returned unknown status {status['status']!r}")
            self.pause()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--minimum", action="store_true", help="only the IC-3 milestone: D twice, C and B once")
    mode.add_argument("--demo", action="store_true", help="resume or run the next full-size D-s0 generation")
    parser.add_argument("--include-b-seeds", action="store_true")
    parser.add_argument("--plan-only", action="store_true", help="show run order without starting jobs")
    parser.add_argument(
        "--preflight", action="store_true", help="check services, base and eval_v1 without starting jobs"
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--poll-seconds", type=float, default=10)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--max-hours", type=float, default=8)
    args = parser.parse_args(argv)
    if not 0 < args.poll_seconds <= 60 or args.retries < 0 or args.max_hours <= 0:
        parser.error("poll-seconds must be in (0, 60], retries >= 0 and max-hours > 0")
    settings = get_settings()
    output = args.output_dir or REPO_ROOT / "runs" / ("demo" if args.demo else "campaign")
    try:
        with httpx.Client(timeout=30) as http:
            services = Services(
                os.environ.get("TRAIN_SERVICE_URL", "http://localhost:8003"),
                settings.env_service_url,
                settings.agent_service_url,
                http=http,
            )
            if args.demo:
                records = TypeAdapter(list[GenerationRecord]).validate_python(
                    services.call("GET", "/generations?arm=D&seed=0")
                )
                plan = [demo_target(records)]
                output = args.output_dir or REPO_ROOT / "runs" / "demo" / plan[0].generation_id
            else:
                plan = experiment_plan(minimum=args.minimum, include_b_seeds=args.include_b_seeds)
            print("Run order: " + ", ".join(target.generation_id for target in plan), flush=True)
            if args.plan_only:
                return 0
            services.preflight()
            if args.preflight:
                print("Preflight passed: three services healthy, base registered, frozen eval_v1 has 300 tasks")
                return 0
            Campaign(
                services, output, poll_seconds=args.poll_seconds, retries=args.retries, max_hours=args.max_hours
            ).run(plan)
        return 0
    except (CampaignError, OSError, ValueError) as error:
        print(f"campaign stopped: {error}\nEvidence and resume state: {output}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(
            f"driver interrupted; server jobs continue. Resume with the same command. State: {output}", file=sys.stderr
        )
        return 130


if __name__ == "__main__":
    sys.exit(main())
