"""Veri backend (scar_train.veri) against a fake SDK client that records calls: no network, no credits."""

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from scar_schemas.models import ChatMessage, ToolCall
from scar_schemas.openai_format import load_tools, to_openai_messages
from veri_sdk.client import VeriRequestError, VeriServerError

from scar_train import veri
from scar_train.config import REPO_ROOT
from scar_train.trainer import Deployment, TrainedAdapter

from .factories import model_version

BASE = "Qwen/Qwen3-4B-Instruct-2507"
VERSION = "D-s0-g1"
API = "https://api.veri.studio"
DEP_URL = f"{API}/v1/deployments/dep_1"


def no_sleep(_: float) -> None:
    pass


def transcript() -> list[dict]:
    """A real-shaped passed run: 3 assistant turns, compact dumps_args arguments, a non-ASCII vendor name."""
    calls = [
        ToolCall(id="c1", name="get_vendors", arguments={"name_contains": "Café"}),
        ToolCall(id="c2", name="get_transactions", arguments={"vendor_id": "co-1:vend-cafe", "start_date": "2026-05-01"}),
        ToolCall(id="c3", name="submit_answer", arguments={"number": 12.5}),
    ]
    msgs = [
        ChatMessage(role="system", content="You are a bookkeeping assistant."),
        ChatMessage(role="user", content="Company: Co\n\nHow much did we spend at Café Rio?"),
        ChatMessage(role="assistant", tool_calls=[calls[0]]),
        ChatMessage(role="tool", tool_call_id="c1", name="get_vendors", content='[{"vendor_id": "co-1:vend-cafe"}]'),
        ChatMessage(role="assistant", content="Looking up transactions.", tool_calls=[calls[1]]),
        ChatMessage(role="tool", tool_call_id="c2", name="get_transactions", content='[{"amount": "12.50"}]'),
        ChatMessage(role="assistant", tool_calls=[calls[2]]),
    ]
    return to_openai_messages(msgs)


def spec_line(messages: list[dict] | None = None) -> dict:
    """One SPEC 9.8 export line, as data_builder.export_jsonl writes it."""
    return {"messages": messages or transcript(), "tools": load_tools()}


def write_spec(path: Path, lines: list[dict]) -> Path:
    path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines), encoding="utf-8")
    return path


class FakeTokenizer:
    """100 tokens per message plus 1 per tool; records that tools arrive as a parsed list."""

    def __init__(self, per_message: int = 100):
        self.per_message = per_message

    def apply_chat_template(self, messages, tools=None, tokenize=True):
        assert tokenize and isinstance(tools, list)
        return list(range(self.per_message * len(messages) + len(tools)))


class FakeVeri:
    """Stands in for veri_sdk.Client: records every call; statuses advance one step per get() and stick at the last."""

    api_url = API

    def __init__(
        self,
        *,
        job_statuses=("queued", "running", "completed"),
        job_error=None,
        model_statuses=("importing", "ready"),
        dep_statuses=("serving",),
        adapter_statuses=("attaching", "active"),
        adapter_replicas=(),
        jobs=(),
        attachments=(),
        deployments=(),
        create_error=None,
    ):
        self.calls: list[tuple[str, tuple, dict]] = []
        self.job_statuses, self.job_error = list(job_statuses), job_error
        self.model_statuses = list(model_statuses)
        self.dep_statuses = list(dep_statuses)
        self.adapter_statuses = list(adapter_statuses)
        self.adapter_replicas = list(adapter_replicas)
        self.jobs, self.attachments, self.deployment_list = list(jobs), list(attachments), list(deployments)
        self.create_error = create_error
        self.uploaded: list[str] = []
        self.created_hyperparameters = None
        self.datasets = SimpleNamespace(upload=self._rec("datasets.upload", self._upload))
        self.training_jobs = SimpleNamespace(
            list=self._rec("training_jobs.list", lambda **kw: self.jobs),
            create=self._rec("training_jobs.create", self._create_job),
            get=self._rec("training_jobs.get", self._get_job),
            cancel=self._rec("training_jobs.cancel", lambda job_id: None),
        )
        self.models = SimpleNamespace(
            create_from_training_job=self._rec("models.create_from_training_job", self._save),
            get=self._rec("models.get", lambda model_id: self._model()),
        )
        self.deployments = SimpleNamespace(
            get=self._rec("deployments.get", self._get_dep),
            wake=self._rec("deployments.wake", lambda dep_id: self._dep("waking")),
            list=self._rec("deployments.list", lambda **kw: self.deployment_list),
            adapters=self._rec("deployments.adapters", self._adapters),
            attach_adapter=self._rec("deployments.attach_adapter", self._attach),
        )
        self.billing = SimpleNamespace(
            balance=self._rec("billing.balance", lambda: {"balance_usd": 20.0}),
            burn_rate=self._rec("billing.burn_rate", lambda: {"burn_rate_usd_per_hour": 0.0}),
        )
        self.cost = SimpleNamespace(estimate=self._rec("cost.estimate", lambda *a, **kw: {"estimated_cost_usd": 1.05}))
        self._http = SimpleNamespace(post=self._rec("http.post", self._post))

    def me(self):
        self.calls.append(("me", (), {}))
        return {"email": "team@example.com", "workspace": {"id": "ws_1", "name": "Personal", "kind": "personal"}}

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]

    def call(self, name: str) -> tuple[tuple, dict]:
        return next((a, kw) for n, a, kw in self.calls if n == name)

    def _rec(self, name, fn):
        def wrapper(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return fn(*args, **kwargs)

        return wrapper

    @staticmethod
    def _next(statuses: list[str]) -> str:
        return statuses.pop(0) if len(statuses) > 1 else statuses[0]

    def _upload(self, path, name=None):
        self.uploaded.append(Path(path).read_text(encoding="utf-8"))
        return SimpleNamespace(id="ds_1", name=name)

    def _job(self, status):
        return SimpleNamespace(
            id="job_1",
            status=status,
            output_name=VERSION,
            base_model=BASE,
            dataset_id="ds_1",
            dataset_snapshot_id="ds_1@snap-1",
            hyperparameters=self.created_hyperparameters,
            current_step=None,
            total_steps=None,
            current_loss=None,
            error=self.job_error if status == "failed" else None,
            cost_usd=0.42 if status == "completed" else None,
            duration_seconds=300.0 if status == "completed" else None,
            gpu={"type": "H100-80GB", "count": 1},
            provider="vast",
            dashboard_url=None,
        )

    def _create_job(self, **kw):
        if self.create_error:
            raise self.create_error
        self.created_hyperparameters = dict(kw["hyperparameters"])
        return self._job("queued")

    def _get_job(self, job_id):
        status = self._next(self.job_statuses)
        if isinstance(status, BaseException):
            raise status
        return self._job(status)

    def _model(self):
        return SimpleNamespace(id="mdl_1", status=self._next(self.model_statuses))

    def _save(self, job_id, name, artifact=None):
        return self._model()

    def _dep(self, status):
        return SimpleNamespace(
            id="dep_1", status=status, name="scar-base", failure_code=None, error=None, cost_per_hour_usd=1.05
        )

    def _get_dep(self, dep_id):
        return self._dep(self._next(self.dep_statuses))

    def _adapters(self, dep_id):
        if not self.attachments:
            return []
        status = self._next(self.adapter_statuses)
        return [dict(a, status=status, replicas=self.adapter_replicas) for a in self.attachments]

    def _attach(self, dep_id, *, serve_name, model_id):
        self.attachments = [{"serve_name": serve_name, "model_id": model_id, "status": "attaching"}]
        return self.attachments[0]

    def _post(self, path, json):
        return httpx.Response(201, json={"id": "dep_1", "status": "queued"})


def make_trainer(fake: FakeVeri, **kw) -> veri.VeriTrainer:
    return veri.VeriTrainer(
        fake,
        gpu_type="H100-80GB",
        gpu_count=1,
        provider="vast",
        hyperparameters=veri.DEFAULT_HYPERPARAMETERS,
        tokenizer_loader=lambda _: FakeTokenizer(),
        sleep=no_sleep,
        **kw,
    )


def make_deployer(fake: FakeVeri, **kw) -> veri.VeriDeployer:
    return veri.VeriDeployer(fake, deployment_id="dep_1", base_url=DEP_URL, sleep=no_sleep, **kw)


def train(trainer: veri.VeriTrainer, tmp_path: Path) -> TrainedAdapter:
    data = write_spec(tmp_path / f"{VERSION}.jsonl", [spec_line()])
    return trainer.train(version=VERSION, dataset_path=data, n_examples=1, base_model_hf_id=BASE, seed=1)


# ---------- veri_rows and convert_dataset ----------


def test_veri_rows_one_row_per_assistant_turn():
    line = spec_line()
    before = json.dumps(line)
    rows = veri.veri_rows(line)

    assert json.dumps(line) == before  # the export line is not modified
    assert len(rows) == 3
    assert [len(r["prompt"]) for r in rows] == [2, 4, 6]
    assert all(len(r["completion"]) == 1 and r["completion"][0]["role"] == "assistant" for r in rows)
    assert all(m["content"] is None for r in rows[::2] for m in r["completion"])
    assert rows[1]["completion"][0]["content"] == "Looking up transactions."
    assert all("content" in m for r in rows for m in r["prompt"] + r["completion"])
    # each prompt is exactly the previous row's prompt + completion + the tool result (one inference call per row)
    for prev, row in zip(rows, rows[1:], strict=False):
        assert row["prompt"][: len(prev["prompt"]) + 1] == prev["prompt"] + prev["completion"]
    # arguments as vLLM renders them: json.dumps defaults, key order kept, non-ASCII kept
    args = [r["completion"][0]["tool_calls"][0]["function"]["arguments"] for r in rows]
    assert args == [
        '{"name_contains": "Café"}',
        '{"start_date": "2026-05-01", "vendor_id": "co-1:vend-cafe"}',
        '{"number": 12.5}',
    ]
    assert rows[2]["prompt"][2]["tool_calls"][0]["function"]["arguments"] == args[0]
    assert all(isinstance(r["tools"], str) and json.loads(r["tools"]) == load_tools() for r in rows)


def test_veri_rows_keeps_raw_arguments():
    msgs = to_openai_messages(
        [
            ChatMessage(role="user", content="q"),
            ChatMessage(role="assistant", tool_calls=[ToolCall(id="a", name="sql_query", arguments={"__raw__": "{bad"})]),
            ChatMessage(role="tool", tool_call_id="a", name="sql_query", content="error"),
            ChatMessage(role="assistant", tool_calls=[ToolCall(id="b", name="sql_query", arguments={"__raw__": "[1,2]"})]),
        ]
    )
    rows = veri.veri_rows(spec_line(msgs))
    assert [r["completion"][0]["tool_calls"][0]["function"]["arguments"] for r in rows] == ["{bad", "[1,2]"]


def test_convert_dataset_drops_rows_longer_than_max_seq_length(tmp_path):
    spec = tmp_path / "d.jsonl"
    write_spec(spec, [spec_line()])
    spec.write_text(spec.read_text() + "\n")  # a blank line is skipped
    out = tmp_path / "rows.jsonl"
    n_tools = len(load_tools())

    stats = veri.convert_dataset(spec, out, max_seq_length=500 + n_tools, tokenizer=FakeTokenizer())

    # rows of 3, 5 and 7 messages: 300, 500 and 700 tokens plus the tools; a row exactly at the limit is kept
    assert stats == veri.ConversionStats(rows=2, dropped_too_long=1)
    written = [json.loads(x) for x in out.read_text().splitlines()]
    assert written == veri.veri_rows(spec_line())[:2]


# ---------- VeriTrainer ----------


def test_trainer_uploads_trains_and_saves_the_adapter(tmp_path, caplog):
    fake = FakeVeri()
    with caplog.at_level(logging.INFO, logger="scar_train.veri"):
        adapter = train(make_trainer(fake), tmp_path)

    assert adapter == TrainedAdapter(adapter_uri="mdl_1", training_job_id="job_1")
    assert fake.names() == [
        "datasets.upload",
        "training_jobs.list",
        "training_jobs.create",
        "training_jobs.get",
        "training_jobs.get",
        "training_jobs.get",
        "models.create_from_training_job",
        "models.get",
    ]
    (path,), kw = fake.call("datasets.upload")
    assert path.endswith(f"{VERSION}.veri.jsonl") and kw["name"].startswith(f"{VERSION}-")
    assert [json.loads(x) for x in fake.uploaded[0].splitlines()] == veri.veri_rows(spec_line())
    _, kw = fake.call("training_jobs.create")
    assert kw == {
        "base_model": BASE,
        "dataset_id": "ds_1",
        "output_name": VERSION,
        "method": "sft_text",
        "gpu_type": "H100-80GB",
        "gpu_count": 1,
        "provider": "vast",
        "hyperparameters": {
            "lora_rank": 16,
            "lora_alpha": 32,
            "lora_dropout": 0.05,
            "learning_rate": 1e-4,
            "num_epochs": 2,
            "max_seq_length": 4096,
        },
    }
    (job_id,), kw = fake.call("models.create_from_training_job")
    assert job_id == "job_1" and kw["artifact"] == "adapter" and kw["name"].startswith(VERSION)
    assert "duration_seconds=300.0 cost_usd=0.42" in caplog.text
    assert "lineage seed 1 is not sent" in caplog.text


def test_trainer_warns_when_the_job_echo_drops_a_hyperparameter(tmp_path, caplog):
    fake = FakeVeri()
    original = fake._create_job

    def create_dropping_dropout(**kw):
        job = original(**kw)
        fake.created_hyperparameters.pop("lora_dropout")
        job.hyperparameters = fake.created_hyperparameters
        return job

    fake.training_jobs.create = create_dropping_dropout
    with caplog.at_level(logging.WARNING, logger="scar_train.veri"):
        train(make_trainer(fake), tmp_path)
    assert "echoes lora_dropout=None, requested 0.05" in caplog.text


def test_trainer_reuses_a_job_for_the_same_version_and_dataset(tmp_path):
    fake = FakeVeri(job_statuses=("completed",))
    fake.jobs = [fake._job("running")]
    adapter = train(make_trainer(fake), tmp_path)
    assert "training_jobs.create" not in fake.names()
    assert adapter.training_job_id == "job_1"


def test_trainer_raises_when_the_job_fails(tmp_path):
    fake = FakeVeri(job_statuses=("running", "failed"), job_error={"code": "oom", "message": "CUDA out of memory"})
    with pytest.raises(veri.VeriBackendError, match="job_1 failed: oom: CUDA out of memory"):
        train(make_trainer(fake), tmp_path)
    assert not any(n.startswith("models.") for n in fake.names())


def test_trainer_cancels_the_job_on_timeout_and_interrupt(tmp_path):
    fake = FakeVeri(job_statuses=("running",))
    with pytest.raises(veri.VeriTimeout):
        train(make_trainer(fake, poll_s=10, timeout_s=30), tmp_path)
    assert fake.names().count("training_jobs.get") == 3
    assert fake.call("training_jobs.cancel")[0] == ("job_1",)

    fake = FakeVeri(job_statuses=(KeyboardInterrupt(),))
    with pytest.raises(KeyboardInterrupt):
        train(make_trainer(fake), tmp_path)
    assert "training_jobs.cancel" in fake.names()


def test_trainer_retries_transient_poll_errors(tmp_path):
    fake = FakeVeri(job_statuses=(VeriServerError("bad gateway"), "running", "completed"))
    assert train(make_trainer(fake), tmp_path).training_job_id == "job_1"


def test_trainer_reports_api_errors_with_context(tmp_path):
    error = VeriRequestError("invalid hyperparameters", code="invalid_hyperparameters")
    fake = FakeVeri(create_error=error)
    with pytest.raises(veri.VeriBackendError, match=r"creating the training job for D-s0-g1: .*invalid_hyperparameters"):
        train(make_trainer(fake), tmp_path)


def test_trainer_refuses_an_empty_dataset(tmp_path):
    fake = FakeVeri()
    trainer = make_trainer(fake)
    trainer.tokenizer_loader = lambda _: FakeTokenizer(per_message=10_000)
    with pytest.raises(veri.VeriBackendError, match="no training rows"):
        train(trainer, tmp_path)
    assert fake.calls == []


# ---------- VeriDeployer ----------


def test_deployer_wakes_attaches_and_waits_until_active():
    fake = FakeVeri(dep_statuses=("scaled_to_zero", "waking", "serving"), adapter_statuses=("attaching", "active"))
    dep = make_deployer(fake).deploy(
        version=VERSION, adapter=TrainedAdapter("mdl_1", "job_1"), base=model_version(endpoint_base_url=DEP_URL)
    )
    assert dep == Deployment(endpoint_base_url=DEP_URL, endpoint_model_id=VERSION)
    assert fake.names().count("deployments.wake") == 1
    assert fake.call("deployments.attach_adapter") == (("dep_1",), {"serve_name": VERSION, "model_id": "mdl_1"})
    assert fake.names()[-2:] == ["deployments.adapters", "deployments.adapters"]


def test_deployer_reuses_an_identical_attachment_and_refuses_a_different_one():
    fake = FakeVeri(attachments=[{"serve_name": VERSION, "model_id": "mdl_1", "status": "active"}])
    adapter = TrainedAdapter("mdl_1", "job_1")
    make_deployer(fake).deploy(version=VERSION, adapter=adapter, base=model_version())
    assert "deployments.attach_adapter" not in fake.names()

    fake = FakeVeri(attachments=[{"serve_name": VERSION, "model_id": "mdl_other", "status": "active"}])
    with pytest.raises(veri.VeriBackendError, match="already attached"):
        make_deployer(fake).deploy(version=VERSION, adapter=adapter, base=model_version())


def test_deployer_attach_timeout_and_load_failure():
    adapter = TrainedAdapter("mdl_1", "job_1")
    fake = FakeVeri(adapter_statuses=("attaching",))
    with pytest.raises(veri.VeriTimeout, match="D-s0-g1 to become active"):
        make_deployer(fake, poll_s=5, attach_timeout_s=20).deploy(version=VERSION, adapter=adapter, base=model_version())
    assert fake.names().count("deployments.adapters") == 1 + 4

    failed = [{"replica_id": "rep_1", "state": "failed", "error": "rank 32 > max_lora_rank"}]
    fake = FakeVeri(adapter_statuses=("attaching",), adapter_replicas=failed)
    with pytest.raises(veri.VeriBackendError, match="failed to load on replica rep_1: rank 32"):
        make_deployer(fake).deploy(version=VERSION, adapter=adapter, base=model_version())


def test_deployer_raises_on_a_failed_deployment():
    fake = FakeVeri(dep_statuses=("failed",))
    with pytest.raises(veri.VeriBackendError, match="dep_1 is failed"):
        make_deployer(fake).deploy(version=VERSION, adapter=TrainedAdapter("mdl_1", "job_1"), base=model_version())
    assert "deployments.attach_adapter" not in fake.names()


# ---------- factory, create-base, tool call, smoke ----------


def test_make_backends_reads_the_environment(monkeypatch):
    monkeypatch.setenv("VERI_API_KEY", "vk_test")
    monkeypatch.delenv("VERI_API_URL", raising=False)
    monkeypatch.setenv("VERI_DEPLOYMENT_ID", "dep_9")
    for name in ("VERI_TRAIN_GPU_TYPE", "VERI_TRAIN_GPU_COUNT", "VERI_TRAIN_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    trainer, deployer = veri.make_backends(None)
    assert (trainer.gpu_type, trainer.gpu_count, trainer.provider) == ("H100-80GB", 1, "vast")
    assert trainer.hyperparameters == veri.DEFAULT_HYPERPARAMETERS
    assert (deployer.deployment_id, deployer.base_url) == ("dep_9", f"{API}/v1/deployments/dep_9")

    monkeypatch.setenv("VERI_TRAIN_GPU_TYPE", "L4-24GB")
    monkeypatch.setenv("VERI_TRAIN_PROVIDER", "aws")
    trainer, _ = veri.make_backends(None)
    assert (trainer.gpu_type, trainer.provider) == ("L4-24GB", "aws")

    monkeypatch.delenv("VERI_DEPLOYMENT_ID")
    with pytest.raises(veri.VeriBackendError, match="VERI_DEPLOYMENT_ID"):
        veri.make_backends(None)


def test_create_base_creates_a_scale_to_zero_multi_adapter_deployment():
    fake = FakeVeri(dep_statuses=("provisioning", "serving"))
    checked, out = [], []
    dep = veri.create_base(
        fake,
        name="scar-base",
        base_model_hf_id=BASE,
        scale_to_zero_window_s=900,
        api_key="key",
        api_key_name="MODEL_API_KEY",
        echo=out.append,
        check=lambda *a: checked.append(a) or (["get_accounts({})"], 0.5),
        sleep=no_sleep,
    )
    assert dep.id == "dep_1"
    (path,), kw = fake.call("http.post")
    assert path == "/v1/deployments"
    assert kw["json"] == {
        "model": BASE,
        "source": "huggingface",
        "name": "scar-base",
        "gpu": {"gpu_type": "L4-24GB", "gpu_count": 1},
        "provider": "aws",
        "serving_mode": "multi_adapter",
        "max_lora_rank": 16,
        "max_loras": 8,
        "min_replicas": 0,
        "max_replicas": 1,
        "scale_to_zero_window_seconds": 900,
        "vllm_extra_args": ["--enable-auto-tool-choice", "--tool-call-parser", "hermes", "--max-model-len", "40960"],
    }
    assert checked == [(DEP_URL, "scar-base", "key")]
    assert (
        f"  3. cd train && uv run python -m scar_train.register_base --endpoint-base-url {DEP_URL}"
        " --endpoint-model-id scar-base"
    ) in out


def test_create_base_reuses_a_live_deployment_with_the_same_name():
    fake = FakeVeri(deployments=[SimpleNamespace(id="dep_1", name="scar-base", status="scaled_to_zero")])
    fake.dep_statuses = ["scaled_to_zero", "serving"]
    veri.create_base(
        fake,
        name="scar-base",
        base_model_hf_id=BASE,
        scale_to_zero_window_s=900,
        api_key="key",
        api_key_name="VERI_API_KEY",
        echo=lambda _: None,
        check=lambda *a: (["get_accounts({})"], 0.5),
        sleep=no_sleep,
    )
    assert "http.post" not in fake.names() and "deployments.wake" in fake.names()


def _chat_response(message: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "x",
            "object": "chat.completion",
            "created": 0,
            "model": "D-s0-g1@v1-abc",
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": message}],
        },
    )


def test_tool_call_parses_tool_calls_and_rejects_plain_answers():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), json.loads(request.content)))
        call = {"id": "c", "type": "function", "function": {"name": "get_accounts", "arguments": "{}"}}
        return _chat_response({"role": "assistant", "content": None, "tool_calls": [call]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    calls, _ = veri.tool_call(DEP_URL, VERSION, "key", http_client=client)
    assert calls == ["get_accounts({})"]
    assert seen[0][0] == f"{DEP_URL}/chat/completions"
    assert seen[0][1]["model"] == VERSION and seen[0][1]["tools"] == load_tools()

    plain = httpx.Client(
        transport=httpx.MockTransport(lambda r: _chat_response({"role": "assistant", "content": "42"}))
    )
    with pytest.raises(veri.VeriBackendError, match="without a tool call"):
        veri.tool_call(DEP_URL, VERSION, "key", http_client=plain)


def test_smoke_runs_every_step_against_fakes(tmp_path):
    fake = FakeVeri()
    trainer = make_trainer(fake)
    trainer.hyperparameters["max_steps"] = veri.SMOKE_MAX_STEPS
    checked = []
    out = veri.smoke(
        fake,
        trainer,
        make_deployer(fake),
        base_model_hf_id=BASE,
        api_key="key",
        workdir=tmp_path,
        task=veri.smoke_task(REPO_ROOT / "fixtures" / "tasks_seed.jsonl"),
        echo=lambda _: None,
        check=lambda url, model, key: checked.append((url, model)) or (["get_accounts({})"], 0.5),
    )
    assert checked == [(DEP_URL, "scar-base"), (DEP_URL, veri.SMOKE_VERSION)]
    assert len(fake.uploaded[0].splitlines()) == 3  # one row per assistant turn of the toy transcript
    assert fake.call("training_jobs.create")[1]["hyperparameters"]["max_steps"] == 2
    assert fake.call("deployments.attach_adapter")[1] == {"serve_name": "D-smoke-g1", "model_id": "mdl_1"}
    assert (out["training_job_id"], out["job_cost_usd"], out["balance_before_usd"]) == ("job_1", 0.42, 20.0)
