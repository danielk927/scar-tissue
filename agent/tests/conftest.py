"""Fakes for the model endpoint, env service, database and Jev."""

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from scar_schemas.models import (
    ExpectedAnswer,
    ModelVersion,
    Origin,
    RunRequest,
    Split,
    Task,
    TaskFamily,
    ToolExecuteResult,
    VerifyResult,
)

from scar_agent.clients import Endpoint, ToolInfraError
from scar_agent.jev import ChoiceResult, Triage
from scar_agent.loop import AttemptContext

SECRET_EXPECTED = Decimal("98765.43")  # must never appear in anything sent to the model


def make_task(task_id: str = "task_1", template_id: str = "agg.total_by_account_period",
              origin: Origin = Origin.seed) -> Task:
    return Task(
        task_id=task_id, company_id="company-004", split=Split.train, family=TaskFamily.aggregation,
        template_id=template_id, params={"account_id": "company-004:acct-1"},
        prompt="What was total spend in Rent from 2026-01-01 to 2026-03-31?",
        expected=ExpectedAnswer(answer_type="number", number=SECRET_EXPECTED),
        difficulty=1, origin=origin, created_at=datetime.now(timezone.utc))


# ---------- model ----------

def tool_call(name: str, args: dict | str, call_id: str = "c1") -> dict:
    raw = args if isinstance(args, str) else json.dumps(args)
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": raw}}


def assistant(content: str | None = None, calls: list[dict] | None = None) -> dict:
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if calls:
        msg["tool_calls"] = calls
    return msg


class _Msg:
    def __init__(self, d: dict):
        self.d = d
        self.content = d.get("content")

    def model_dump(self) -> dict:
        return dict(self.d)


class FakeLLM:
    """Scripted chat.completions: each item is a message dict, an exception, or a coroutine fn."""

    def __init__(self, script: list):
        self.script = list(script)
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.requests.append(kwargs)
        item = self.script.pop(0) if self.script else assistant("I give up")
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            item = await item()
        return SimpleNamespace(choices=[SimpleNamespace(message=_Msg(item))],
                               usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5))


# ---------- env ----------

class FakeEnv:
    def __init__(self, tool_result: Any = None, tool_ok: bool = True, tool_error: str | None = None,
                 infra_fail: bool = False, expected_number: float = 100.0):
        self.tool_result = tool_result if tool_result is not None else [{"account_id": "a1", "name": "Rent"}]
        self.tool_ok, self.tool_error, self.infra_fail = tool_ok, tool_error, infra_fail
        self.expected_number = expected_number
        self.tool_calls: list = []
        self.verifies: list = []
        self.tasks: dict[str, Task] = {}

    async def get_task(self, task_id: str) -> Task:
        return self.tasks.get(task_id) or make_task(task_id)

    async def execute_tool(self, req):
        self.tool_calls.append(req)
        if self.infra_fail:
            raise ToolInfraError("HTTP 503")
        if self.tool_ok:
            return ToolExecuteResult(ok=True, result=self.tool_result)
        return ToolExecuteResult(ok=False, error=self.tool_error)

    async def verify(self, req):
        self.verifies.append(req)
        if req.stop_reason != "submitted":
            return VerifyResult(passed=False, detail=f"not submitted: {req.stop_reason}")
        got = (req.final_answer or {}).get("number")
        return VerifyResult(passed=got == self.expected_number,
                            detail=f"number: expected {self.expected_number}, got {got}")


def make_ctx(llm: FakeLLM, env: FakeEnv, **req_kwargs) -> AttemptContext:
    req_kwargs.setdefault("purpose", "traffic")
    req = RunRequest(task_id="task_1", model_version="base", **req_kwargs)
    return AttemptContext(req=req, task=make_task(), company_name="Stitchform Apparel Co",
                          prompt_text="SYSTEM PROMPT", endpoint_model_id="qwen-test", llm=llm, env=env)


# ---------- store ----------

class FakeStore:
    def __init__(self):
        self.runs: list = []
        self.failures: list = []
        self.task_meta: dict[str, tuple[str, str]] = {}
        self.catalog = {"accounts": [{"account_id": "company-004:acct-1", "name": "Rent", "account_type": "expense"}],
                        "vendors": [{"vendor_id": "company-004:v-1", "name": "Acme Supplies"}]}

    async def insert_run(self, r):
        self.runs.append(r)

    async def get_company_name(self, company_id):
        return "Stitchform Apparel Co"

    async def get_prompt_text(self, v):
        return None

    async def get_model_version(self, v):
        if v != "base":
            return None
        return ModelVersion(version="base", arm=None, seed=None, generation=0, parent_version=None,
                            base_model_hf_id="x", endpoint_base_url="http://model", endpoint_model_id="qwen-test",
                            adapter_uri=None, training_job_id=None, snapshot_id=None, status="promoted",
                            created_at=datetime.now(timezone.utc))

    async def failed_runs_to_mine(self, arm, seed, generation):
        mined = {f.run_id for f in self.failures}
        return sorted((r for r in self.runs if r.arm == arm and r.seed == seed and r.generation == generation
                       and r.purpose in ("traffic", "judge") and not r.passed and r.run_id not in mined),
                      key=lambda r: r.run_id)

    async def get_task_meta(self, task_ids):
        return {t: self.task_meta.get(t, ("agg.total_by_account_period", "question?")) for t in task_ids}

    async def insert_failures(self, records):
        self.failures.extend(records)

    async def save_mined(self, arm, seed, generation, new, recap):
        known = {f.run_id for f in self.failures}
        self.failures.extend(f for f in new if f.run_id not in known)
        mine = [f for f in self.failures if (f.arm, f.seed, f.generation) == (arm, seed, generation)]
        updated = {f.failure_id: f for f in recap(mine)}
        self.failures = [updated.get(f.failure_id, f) for f in self.failures]
        return [updated[f.failure_id] for f in mine]

    async def list_failures(self, arm=None, seed=None, generation=None, included=None):
        return [f for f in self.failures
                if (arm is None or f.arm == arm) and (seed is None or f.seed == seed)
                and (generation is None or f.generation == generation)
                and (included is None or f.included == included)]

    async def get_company_catalog(self, company_id):
        return self.catalog


# ---------- jev ----------

class FakeJev:
    def __init__(self, triage_by_task: dict[str, Triage] | None = None, choice: ChoiceResult | None = None):
        self.triage_by_task = triage_by_task or {}
        self.choice = choice
        self.triage_calls: list[dict] = []
        self.choose_calls: list = []

    async def triage(self, state: dict) -> Triage:
        self.triage_calls.append(state)
        await asyncio.sleep(0)
        return self.triage_by_task.get(
            state["task"]["question"],
            Triage("reasoning_error", 0.8, {"reasoning_error": 0.8, "other": 0.2}, 0.9, "jev-1.14.0"))

    async def choose(self, state, instructions, criteria):
        self.choose_calls.append((state, instructions, criteria))
        return self.choice


def fake_llm_factory(llm: FakeLLM):
    def factory(ep: Endpoint):
        return llm
    return factory


@pytest.fixture
def store() -> FakeStore:
    return FakeStore()
