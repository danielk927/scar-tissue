"""Veri backend (SPEC 9.8, P3-05, P3-08; facts and evidence in docs/VERI.md): managed `sft_text` LoRA training and ONE
multi-adapter deployment that serves `base` plus every adapter version by `model` name (D-03, D-04).

Per candidate version (for example D-s0-g1):
1. veri_rows / convert_dataset turn each SPEC 9.8 export line into one prompt/completion row per assistant turn (the
   SPEC 9.8 Veri export). Rows longer than max_seq_length tokens, counted exactly with the base model's tokenizer and
   chat template with the tools, are dropped, so truncation never cuts a completion.
2. VeriTrainer uploads the rows, runs a managed sft_text LoRA job (output_name = version), waits for it, and saves the
   adapter as a library model (artifact="adapter"). TrainedAdapter.adapter_uri is that library model id, which is what
   attach needs. It is idempotent per version: a completed or in-flight job of that version is reused, so a re-run
   (an interrupted loop step, or smoke run twice) never pays twice; a new job starts only after a failed one.
3. VeriDeployer wakes the deployment if it is parked, attaches the model under serve_name=veri_name(version) (an
   attachment of the same model under that name is accepted as is), waits until it is active, and returns the
   deployment's OpenAI-compatible base URL with endpoint_model_id=veri_name(version).

CLI (run from train/; the repo-root .env is loaded):
    uv run python -m scar_train.veri whoami        # account, balance, burn rate: verifies VERI_API_KEY
    uv run python -m scar_train.veri create-base   # multi-adapter deployment of BASE_MODEL_HF_ID on 1x L4 (aws)
    uv run python -m scar_train.veri wake          # wake the parked deployment and wait until it serves
    uv run python -m scar_train.veri smoke         # P3-05 smoke: base tool call, toy job, attach D-smoke-g1, tool call

Environment (repo-root .env):
    VERI_API_KEY                       required, read by the SDK (VERI_API_URL overrides https://api.veri.studio)
    BASE_MODEL_HF_ID                   model to serve and train (D-04: Qwen/Qwen3-4B-Instruct-2507)
    VERI_DEPLOYMENT_ID                 the deployment create-base prints; needed by TRAINER=veri, wake and smoke
    VERI_TRAIN_GPU_TYPE                training GPU, default H100-80GB (fastest and cheapest per job, docs/VERI.md 5)
    VERI_TRAIN_GPU_COUNT               default 1
    VERI_TRAIN_PROVIDER                default vast; the alternative is VERI_TRAIN_GPU_TYPE=L4-24GB with provider aws
    VERI_SCALE_TO_ZERO_WINDOW_SECONDS  create-base only: idle seconds before the deployment parks (default 900, min 300)
    MODEL_API_KEY                      key sent to the deployment by the agent and by smoke's tool calls; the value of
                                       VERI_API_KEY works, a model access key bound to the deployment is narrower

Scale to zero and the cold start: create-base sets min_replicas=0. A replica bills per minute only while its GPU box is
up; after VERI_SCALE_TO_ZERO_WINDOW_SECONDS without requests the deployment parks (free). The next request wakes it and
gets 503 `deployment_waking` for a few minutes while a box boots (billed from launch). The agent's OpenAI client does
not wait that long, so its runs would end as model_error: run `wake` before agent work after an idle period. The
deployer wakes the deployment itself before attaching, so a park during a long training job costs one cold start
instead of idle GPU time. For a long session with no idle gaps, keep it warm with
`Client().deployments.update(VERI_DEPLOYMENT_ID, min_replicas=1)` and set min_replicas back to 0 afterwards.
"""

import argparse
import contextlib
import hashlib
import json
import logging
import math
import os
import re
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
import openai
from openai import OpenAI
from scar_schemas.constants import MAX_TOKENS_PER_TURN, TOOLS_VERSION
from scar_schemas.models import ChatMessage, ModelVersion, ToolCall
from scar_schemas.openai_format import dumps_args, from_openai_message, load_tools, to_openai_messages
from veri_sdk import Client
from veri_sdk.client import VeriError, VeriServerError, _raise

from .config import REPO_ROOT, Settings, get_settings
from .trainer import Deployment, TrainedAdapter

log = logging.getLogger(__name__)

# SPEC 9.8 starting hyperparameters. Loss on assistant tokens only comes from the prompt/completion rows (TRL masks the
# prompt). Managed sft_text has no seed knob: it trains with seed 42 for every lineage (SPEC 9.8 Veri export).
# TODO(VERIFY): the control plane forwards lora_dropout to the runtime. It is not in the API's SftTextHyperparameters
# schema; the runtime reads it (docs/VERI.md 2.3). VeriTrainer logs a warning if the job echo drops or changes it.
DEFAULT_HYPERPARAMETERS: dict[str, Any] = {
    "lora_rank": 16,
    "lora_alpha": 32,
    "lora_dropout": 0.05,
    "learning_rate": 1e-4,
    "num_epochs": 2,
    "max_seq_length": 4096,
}
DEFAULT_TRAIN_GPU_TYPE = "H100-80GB"
DEFAULT_TRAIN_PROVIDER = "vast"

# The deployment create-base makes: 1x L4 on aws, multi-adapter, tool calls parsed by vLLM's hermes parser.
SERVE_GPU = {"gpu_type": "L4-24GB", "gpu_count": 1}
SERVE_PROVIDER = "aws"
DEFAULT_DEPLOYMENT_NAME = "scar-base"
DEFAULT_SCALE_TO_ZERO_WINDOW_S = 900
# Context cap: the base model's native 262,144 tokens of KV cache cannot fit on an L4. 40,960 covers the longest agent
# run (MAX_STEPS turns of MAX_TOKENS_PER_TURN plus tool results of up to TOOL_RESULT_MAX_CHARS).
MAX_MODEL_LEN = 40960
VLLM_EXTRA_ARGS = ["--enable-auto-tool-choice", "--tool-call-parser", "hermes", "--max-model-len", str(MAX_MODEL_LEN)]
# vLLM preallocates max_loras slots of max_lora_rank for every LoRA layer; the API defaults (32 slots, rank 64) would
# take about 8 GB of the L4. An adapter of a higher rank never finishes attaching, so this follows the trained rank.
MAX_LORA_RANK = DEFAULT_HYPERPARAMETERS["lora_rank"]
MAX_LORAS = 8

TRAIN_POLL_S = 30.0
IN_FLIGHT_JOB_STATUSES = ("queued", "provisioning", "configuring", "running")  # the API's JobStatus enum
TRAIN_TIMEOUT_S = 6 * 3600
MODEL_POLL_S = 5.0
MODEL_TIMEOUT_S = 600.0
DEPLOYMENT_POLL_S = 10.0
WAKE_TIMEOUT_S = 1500.0  # the platform's boot ceiling is 1200 s
APPEND_BATCH_BYTES = 1_500_000  # per datasets.append call; whole-file uploads over a few MB fail
ATTACH_POLL_S = 5.0  # requests for an attaching adapter get 503 with Retry-After: 5
ATTACH_TIMEOUT_S = 900.0

SMOKE_VERSION = "D-smoke-g1"
SMOKE_MAX_STEPS = 2
CHECK_SYSTEM_PROMPT = "You are a bookkeeping assistant. Answer by calling the tools."
CHECK_USER_MESSAGE = "Company: Test\n\nList the accounts."


def veri_name(name: str) -> str:
    """Veri resource names (saved models, serve names) allow 1-63 lowercase letters, digits and hyphens, no leading or
    trailing hyphen (found live: "D-smoke-g1-..." was rejected). Registry versions keep the SPEC format (D-s0-g1);
    their served name is this lowercase form, which is what endpoint_model_id holds."""
    cleaned = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")[:63].rstrip("-")
    if not cleaned:
        raise ValueError(f"no valid Veri name for {name!r}")
    return cleaned


class VeriBackendError(RuntimeError):
    """A Veri call failed, a job or attachment ended badly, or the backend is misconfigured."""


class VeriTimeout(VeriBackendError):
    pass


class VeriConfigError(VeriBackendError, ValueError):
    """A required setting is missing (a ValueError, so the /loop routes answer 503 trainer_unavailable)."""


# ---------- dataset conversion (SPEC 9.8 Veri export, docs/VERI.md 2.3) ----------


def veri_rows(line: dict) -> list[dict]:
    """One {"prompt", "completion", "tools"} row per assistant message of one SPEC 9.8 export line.

    Every message gets "content": null when it has none (the runtime's chat detector needs the key); tool-call argument
    strings are re-serialized the way vLLM renders them at inference (json.dumps defaults); arguments that are not a
    JSON object (an agent `__raw__` string) are kept as they are; tools become a JSON string (an Arrow list column
    would merge the tool schemas)."""
    msgs = json.loads(json.dumps(line["messages"]))  # deep copy: the caller's line is not modified
    for m in msgs:
        m.setdefault("content", None)
        for call in m.get("tool_calls") or []:
            call["function"]["arguments"] = _as_served(call["function"]["arguments"])
    tools = json.dumps(line["tools"], ensure_ascii=False)
    return [
        {"prompt": msgs[:k], "completion": [m], "tools": tools} for k, m in enumerate(msgs) if m["role"] == "assistant"
    ]


def _as_served(arguments: str) -> str:
    try:
        parsed = json.loads(arguments)
    except (json.JSONDecodeError, TypeError):
        return arguments
    return json.dumps(parsed, ensure_ascii=False) if isinstance(parsed, dict) else arguments


@dataclass(frozen=True)
class ConversionStats:
    rows: int
    dropped_too_long: int


@lru_cache(maxsize=2)
def load_tokenizer(base_model_hf_id: str) -> Any:
    """The base model's tokenizer and chat template: downloads tokenizer files only, never weights."""
    from transformers import AutoTokenizer  # slow import, needed only when converting

    return AutoTokenizer.from_pretrained(base_model_hf_id)


def count_tokens(tokenizer: Any, row: dict) -> int:
    """Tokens TRL 1.7.1's SFTTrainer feeds the model for a conversational prompt/completion row: the chat template
    applied to prompt + completion with the row's tools (trl/trainer/sft_trainer.py tokenize_fn)."""
    ids = tokenizer.apply_chat_template(
        row["prompt"] + row["completion"], tools=json.loads(row["tools"]), tokenize=True
    )
    return len(ids)


def convert_dataset(
    spec_jsonl: Path, out: Path, *, max_seq_length: int = 4096, tokenizer: Any = None
) -> ConversionStats:
    """Write veri_rows of every line of spec_jsonl to out, dropping rows longer than max_seq_length tokens.
    tokenizer defaults to BASE_MODEL_HF_ID's."""
    if tokenizer is None:
        tokenizer = load_tokenizer(_base_model_hf_id(get_settings()))
    rows = dropped = 0
    with spec_jsonl.open(encoding="utf-8") as src, out.open("w", encoding="utf-8") as dst:
        for line in src:
            if not line.strip():
                continue
            for row in veri_rows(json.loads(line)):
                if count_tokens(tokenizer, row) > max_seq_length:
                    dropped += 1
                    continue
                dst.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
                rows += 1
    return ConversionStats(rows=rows, dropped_too_long=dropped)


# ---------- polling and errors ----------


@contextlib.contextmanager
def _veri_call(what: str) -> Iterator[None]:
    try:
        yield
    except VeriError as e:
        code = f" [{e.code}]" if e.code else ""
        raise VeriBackendError(f"Veri API error while {what}: {e}{code}") from e
    except httpx.HTTPError as e:
        raise VeriBackendError(f"network error while {what}: {e!r}") from e


def _poll(
    fetch: Callable[[], Any],
    check: Callable[[Any], Any],
    *,
    what: str,
    timeout_s: float,
    poll_s: float,
    sleep: Callable[[float], None],
) -> Any:
    """Call fetch() every poll_s seconds until check(value) returns something other than None (check raises on a
    terminal failure). Server errors and network blips while polling are retried; VeriTimeout after timeout_s."""
    for _ in range(max(1, math.ceil(timeout_s / poll_s))):
        try:
            value = fetch()
        except (VeriServerError, httpx.TransportError) as e:
            log.warning("%s: transient error while polling (%r); retrying", what, e)
        else:
            result = check(value)
            if result is not None:
                return result
        sleep(poll_s)
    raise VeriTimeout(f"{what}: not done after {timeout_s:.0f}s")


def endpoint_base_url(client: Client, deployment_id: str) -> str:
    """OpenAI-compatible base URL of a deployment: the OpenAI SDK appends /chat/completions (docs/VERI.md 4)."""
    return f"{client.api_url.rstrip('/')}/v1/deployments/{deployment_id}"


def ensure_serving(
    client: Client,
    deployment_id: str,
    *,
    timeout_s: float = WAKE_TIMEOUT_S,
    poll_s: float = DEPLOYMENT_POLL_S,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """Wait until the deployment serves, waking it if it is parked (scaled_to_zero); returns the SDK Deployment."""
    start = time.monotonic()
    last: str | None = None
    woke = False

    def check(dep: Any) -> Any:
        nonlocal last, woke
        if dep.status != last:
            log.info("deployment %s: %s (%.0fs)", deployment_id, dep.status, time.monotonic() - start)
            last = dep.status
        if dep.status == "serving":
            return dep
        if dep.status in ("failed", "stopped"):
            raise VeriBackendError(f"deployment {deployment_id} is {dep.status}: {dep.failure_code} {dep.error}")
        if dep.status == "scaled_to_zero":
            if woke and dep.failure_code:
                raise VeriBackendError(f"deployment {deployment_id} failed to wake: {dep.failure_code} {dep.error}")
            log.info("deployment %s is parked; waking it (a cold start takes a few minutes)", deployment_id)
            with _veri_call(f"waking deployment {deployment_id}"):
                client.deployments.wake(deployment_id)
            woke = True
        return None

    with _veri_call(f"reading deployment {deployment_id}"):
        return _poll(
            lambda: client.deployments.get(deployment_id),
            check,
            what=f"deployment {deployment_id} to serve",
            timeout_s=timeout_s,
            poll_s=poll_s,
            sleep=sleep,
        )


# ---------- trainer and deployer (trainer.py protocols) ----------


class VeriTrainer:
    """Trainer: managed sft_text LoRA job on Veri, adapter saved as a library model (adapter_uri = its model id)."""

    def __init__(
        self,
        client: Client,
        *,
        gpu_type: str,
        gpu_count: int,
        provider: str | None,
        hyperparameters: dict[str, Any],
        tokenizer_loader: Callable[[str], Any] = load_tokenizer,
        poll_s: float = TRAIN_POLL_S,
        timeout_s: float = TRAIN_TIMEOUT_S,
        model_poll_s: float = MODEL_POLL_S,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.client = client
        self.gpu_type = gpu_type
        self.gpu_count = gpu_count
        self.provider = provider
        self.hyperparameters = dict(hyperparameters)
        self.tokenizer_loader = tokenizer_loader
        self.poll_s = poll_s
        self.timeout_s = timeout_s
        self.model_poll_s = model_poll_s
        self.sleep = sleep

    def train(
        self, *, version: str, dataset_path: Path, n_examples: int, base_model_hf_id: str, seed: int
    ) -> TrainedAdapter:
        """Idempotent per version (the loop re-runs an interrupted training step): a job whose output_name is version
        is reused if it completed or is still in flight; a new job starts only if there is none or all failed."""
        ctx = {"model_version": version}
        job = self._existing_job(version, base_model_hf_id)
        if job is None:
            job = self._submit(version, dataset_path, n_examples, base_model_hf_id, seed, ctx)
        job = self._wait_job(job.id, ctx)
        log.info(
            "veri: training job %s completed: duration_seconds=%s cost_usd=%s gpu=%s provider=%s",
            job.id,
            job.duration_seconds,
            job.cost_usd,
            job.gpu,
            job.provider,
            extra=ctx,
        )
        model = self._save_adapter(job.id, version)
        log.info("veri: adapter of job %s saved as model %s", job.id, model.id, extra=ctx)
        return TrainedAdapter(adapter_uri=model.id, training_job_id=job.id)

    def _submit(
        self, version: str, dataset_path: Path, n_examples: int, base_model_hf_id: str, seed: int, ctx: dict
    ) -> Any:
        """Convert and upload the dataset, then create the sft_text job."""
        rows_path = dataset_path.with_name(f"{dataset_path.stem}.veri.jsonl")
        max_len = self.hyperparameters["max_seq_length"]
        stats = convert_dataset(
            dataset_path, rows_path, max_seq_length=max_len, tokenizer=self.tokenizer_loader(base_model_hf_id)
        )
        log.info(
            "veri: %d examples -> %d per-turn rows, %d dropped as longer than %d tokens",
            n_examples,
            stats.rows,
            stats.dropped_too_long,
            max_len,
            extra=ctx,
        )
        if stats.rows == 0:
            raise VeriBackendError(f"{version}: no training rows left after dropping rows over {max_len} tokens")
        log.info("veri: managed sft_text trains with seed 42; lineage seed %d is not sent (SPEC 9.8)", seed, extra=ctx)

        digest = hashlib.sha256(rows_path.read_bytes()).hexdigest()[:12]
        ds = self._upload_rows(rows_path, veri_name(f"{version}-{digest}"))
        log.info("veri: dataset %s (%d rows)", ds.id, stats.rows, extra=ctx)
        with _veri_call(f"creating the training job for {version}"):
            job = self.client.training_jobs.create(
                base_model=base_model_hf_id,
                dataset_id=ds.id,
                output_name=version,
                method="sft_text",
                gpu_type=self.gpu_type,
                gpu_count=self.gpu_count,
                provider=self.provider,
                hyperparameters=self.hyperparameters,
            )
        log.info(
            "veri: training job %s submitted (%s x%d on %s) %s",
            job.id,
            self.gpu_type,
            self.gpu_count,
            self.provider or "default provider",
            job.dashboard_url or "",
            extra=ctx,
        )
        self._check_echo(job)
        return job

    def _existing_job(self, version: str, base_model_hf_id: str) -> Any:
        """The newest job named version (output_name) on this base model that completed, else one still in flight;
        None if there is none or every one failed or was cancelled."""
        with _veri_call("listing training jobs"):
            jobs = self.client.training_jobs.list(limit=100, method="sft_text")
        mine = [j for j in jobs if j.output_name == version and j.base_model == base_model_hf_id]
        for wanted in (("completed",), IN_FLIGHT_JOB_STATUSES):
            job = next((j for j in mine if j.status in wanted), None)
            if job is not None:
                log.info("veri: reusing training job %s (%s) for %s", job.id, job.status, version)
                return job
        if mine:
            log.info("veri: earlier jobs for %s ended %s; starting a new one", version, [j.status for j in mine])
        return None

    def _check_echo(self, job: Any) -> None:
        echo = job.hyperparameters or {}
        if not echo:
            return
        for key, value in self.hyperparameters.items():
            if echo.get(key) != value:
                log.warning("veri: job %s echoes %s=%r, requested %r", job.id, key, echo.get(key), value)

    def _wait_job(self, job_id: str, ctx: dict) -> Any:
        start = time.monotonic()
        last: tuple | None = None

        def check(job: Any) -> Any:
            nonlocal last
            progress = (job.status, job.current_step, job.total_steps)
            if progress != last:
                log.info(
                    "veri: job %s %s step %s/%s loss %s (%.0fs)",
                    job_id,
                    job.status,
                    job.current_step,
                    job.total_steps,
                    job.current_loss,
                    time.monotonic() - start,
                    extra=ctx,
                )
                last = progress
            if job.status == "completed":
                return job
            if job.status in ("failed", "cancelled"):
                err = job.error or {}
                raise VeriBackendError(f"training job {job_id} {job.status}: {err.get('code')}: {err.get('message')}")
            return None

        try:
            with _veri_call(f"reading training job {job_id}"):
                return _poll(
                    lambda: self.client.training_jobs.get(job_id),
                    check,
                    what=f"training job {job_id}",
                    timeout_s=self.timeout_s,
                    poll_s=self.poll_s,
                    sleep=self.sleep,
                )
        except VeriTimeout:
            self._cancel(job_id)  # stop the GPU bill: a job this late is stuck
            raise
        except KeyboardInterrupt:
            log.warning(
                "veri: interrupted; training job %s keeps running (and billing) and the next train() of this version"
                " reuses it. Stop it with `veri jobs cancel %s`",
                job_id,
                job_id,
            )
            raise

    def _cancel(self, job_id: str) -> None:
        try:
            self.client.training_jobs.cancel(job_id)
            log.warning("veri: cancelled training job %s", job_id)
        except (VeriError, httpx.HTTPError) as e:
            log.error("veri: could not cancel training job %s (%r); cancel it on the dashboard", job_id, e)

    def _upload_rows(self, rows_path: Path, name: str) -> Any:
        """The dataset holding every row of rows_path, as an append-only `completion` stream named `name` (unique per
        file digest). Uploads over a few MB fail with "Error parsing multipart/form-data request" (found live: 8 MB
        failed, 2 MB worked), so rows go in APPEND_BATCH_BYTES batches. Re-running resumes after the stream's head;
        a stream longer than the file is an error, never silently reused."""
        rows = [json.loads(line) for line in rows_path.read_text().splitlines() if line.strip()]
        with _veri_call(f"looking up dataset {name}"):
            existing = [d for d in self.client.datasets.list() if d.name == name]
        if existing:
            ds = existing[0]
        else:
            with _veri_call(f"creating dataset {name}"):
                ds = self.client.datasets.create(name, format="completion")
        head = ds.head_row or 0
        if head > len(rows):
            raise VeriBackendError(
                f"dataset {ds.id} ({name}) has {head} rows, more than the {len(rows)} in {rows_path}"
            )
        batch: list[dict] = []
        size = 0
        for row in rows[head:]:
            n = len(json.dumps(row, ensure_ascii=False))
            if batch and size + n > APPEND_BATCH_BYTES:
                with _veri_call(f"appending {len(batch)} rows to dataset {ds.id}"):
                    self.client.datasets.append(ds.id, rows=batch)
                batch, size = [], 0
            batch.append(row)
            size += n
        if batch:
            with _veri_call(f"appending {len(batch)} rows to dataset {ds.id}"):
                self.client.datasets.append(ds.id, rows=batch)
        with _veri_call(f"reading dataset {ds.id}"):
            ds = self.client.datasets.get(ds.id)
        if (ds.head_row or 0) != len(rows):
            raise VeriBackendError(f"dataset {ds.id} ({name}) ended with {ds.head_row} rows, expected {len(rows)}")
        return ds

    def _save_adapter(self, job_id: str, version: str) -> Any:
        """Library model holding the LoRA adapter (idempotent per job); waits until it is ready to attach."""
        with _veri_call(f"saving the adapter of job {job_id}"):
            model = self.client.models.create_from_training_job(
                job_id, name=veri_name(f"{version}-{job_id}"), artifact="adapter"
            )
        if model.status == "ready":
            return model

        def check(m: Any) -> Any:
            if m.status == "ready":
                return m
            if m.status == "failed":  # TODO(VERIFY): statuses besides "importing" and "ready" (SDK docstrings)
                raise VeriBackendError(f"saving the adapter of job {job_id} as model {m.id} failed")
            return None

        with _veri_call(f"reading model {model.id}"):
            return _poll(
                lambda: self.client.models.get(model.id),
                check,
                what=f"model {model.id} to be ready",
                timeout_s=MODEL_TIMEOUT_S,
                poll_s=self.model_poll_s,
                sleep=self.sleep,
            )


class VeriDeployer:
    """Deployer: attach the adapter to the multi-adapter deployment under serve_name=veri_name(version)."""

    def __init__(
        self,
        client: Client,
        *,
        deployment_id: str,
        base_url: str,
        poll_s: float = ATTACH_POLL_S,
        wake_poll_s: float = DEPLOYMENT_POLL_S,
        attach_timeout_s: float = ATTACH_TIMEOUT_S,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.client = client
        self.deployment_id = deployment_id
        self.base_url = base_url
        self.poll_s = poll_s
        self.wake_poll_s = wake_poll_s
        self.attach_timeout_s = attach_timeout_s
        self.sleep = sleep

    def deploy(self, *, version: str, adapter: TrainedAdapter, base: ModelVersion) -> Deployment:
        ensure_serving(self.client, self.deployment_id, poll_s=self.wake_poll_s, sleep=self.sleep)
        serve_name = veri_name(version)
        with _veri_call(f"listing the adapters of deployment {self.deployment_id}"):
            current = self._attachment(serve_name)
        if current is None:
            with _veri_call(f"attaching model {adapter.adapter_uri} as {serve_name}"):
                self.client.deployments.attach_adapter(
                    self.deployment_id, serve_name=serve_name, model_id=adapter.adapter_uri
                )
            log.info("veri: attaching model %s as %s", adapter.adapter_uri, version, extra={"model_version": version})
        elif current["model_id"] != adapter.adapter_uri:
            raise VeriBackendError(
                f"{version} is already attached to deployment {self.deployment_id} with model {current['model_id']},"
                f" not {adapter.adapter_uri}; version names are never reused"
            )
        with _veri_call(f"listing the adapters of deployment {self.deployment_id}"):
            self._wait_active(serve_name)
        return Deployment(endpoint_base_url=self.base_url, endpoint_model_id=serve_name)

    def _attachment(self, version: str) -> dict | None:
        attachments = self.client.deployments.adapters(self.deployment_id)
        return next((a for a in attachments if a["serve_name"] == version), None)

    def _wait_active(self, version: str) -> dict:
        start = time.monotonic()

        def check(att: dict | None) -> dict | None:
            if att is None:
                raise VeriBackendError(f"{version} disappeared from deployment {self.deployment_id} while attaching")
            if att["status"] == "active":
                log.info("veri: %s active after %.0fs", version, time.monotonic() - start)
                return att
            failed = [r for r in att.get("replicas") or [] if r.get("state") == "failed"]
            if failed:
                raise VeriBackendError(
                    f"{version} failed to load on replica {failed[0].get('replica_id')}: {failed[0].get('error')}"
                )
            return None

        return _poll(
            lambda: self._attachment(version),
            check,
            what=f"{version} to become active on deployment {self.deployment_id}",
            timeout_s=self.attach_timeout_s,
            poll_s=self.poll_s,
            sleep=self.sleep,
        )


def make_backends(settings: Settings) -> tuple[VeriTrainer, VeriDeployer]:
    """The Veri Trainer and Deployer for TRAINER=veri. Configuration comes from the VERI_* variables (module docstring);
    settings is the loop's factory argument (the base model id arrives with each train call)."""
    client = make_client()
    deployment_id = _require_env("VERI_DEPLOYMENT_ID", "the id create-base printed")
    trainer = VeriTrainer(
        client,
        gpu_type=os.environ.get("VERI_TRAIN_GPU_TYPE") or DEFAULT_TRAIN_GPU_TYPE,
        gpu_count=int(os.environ.get("VERI_TRAIN_GPU_COUNT") or 1),
        provider=os.environ.get("VERI_TRAIN_PROVIDER") or DEFAULT_TRAIN_PROVIDER,
        hyperparameters=DEFAULT_HYPERPARAMETERS,
    )
    deployer = VeriDeployer(client, deployment_id=deployment_id, base_url=endpoint_base_url(client, deployment_id))
    return trainer, deployer


def make_client() -> Client:
    try:
        return Client()  # VERI_API_KEY (the repo-root .env is loaded by scar_train.config)
    except ValueError as e:
        raise VeriConfigError("VERI_API_KEY is not set: add it to the repo-root .env") from e


def _require_env(name: str, what: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise VeriConfigError(f"{name} is not set ({what}); add it to the repo-root .env")
    return value


def _base_model_hf_id(settings: Settings) -> str:
    if not settings.base_model_hf_id:
        raise VeriConfigError("BASE_MODEL_HF_ID is not set; D-04 is Qwen/Qwen3-4B-Instruct-2507")
    return settings.base_model_hf_id


# ---------- multi-adapter deployment (create-base) ----------


def fleet_body(base_model_hf_id: str, name: str, scale_to_zero_window_s: int) -> dict:
    """POST /v1/deployments body: a multi-adapter deployment of the base model from Hugging Face on 1x L4 (aws) that
    parks after scale_to_zero_window_s idle seconds (fields from the API's DeploymentCreate schema)."""
    return {
        "model": base_model_hf_id,
        "source": "huggingface",
        "name": name,
        "gpu": dict(SERVE_GPU),
        "provider": SERVE_PROVIDER,
        "serving_mode": "multi_adapter",
        "max_lora_rank": MAX_LORA_RANK,
        "max_loras": MAX_LORAS,
        "min_replicas": 0,
        "max_replicas": 1,
        "scale_to_zero_window_seconds": scale_to_zero_window_s,
        "vllm_extra_args": list(VLLM_EXTRA_ARGS),
    }


def create_fleet(client: Client, body: dict) -> Any:
    """Create the deployment. The SDK's deployments.create() has no serving_mode argument, so this one documented REST
    call goes through the SDK's authenticated HTTP client and error mapping."""
    with _veri_call("creating the multi-adapter deployment"):
        resp = client._http.post("/v1/deployments", json=body)
        _raise(resp)
        return client.deployments.get(resp.json()["id"])


def tool_call(
    base_url: str, model: str, api_key: str, *, http_client: httpx.Client | None = None
) -> tuple[list[str], float]:
    """One chat completion with tools_v1; returns the parsed tool calls and the latency. Raises VeriBackendError unless
    there is a tool call and every call's arguments parse (SPEC 9.8 pre-registration check)."""
    oai = OpenAI(base_url=base_url, api_key=api_key, timeout=120, max_retries=2, http_client=http_client)
    start = time.monotonic()
    try:
        resp = oai.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": CHECK_SYSTEM_PROMPT},
                {"role": "user", "content": CHECK_USER_MESSAGE},
            ],
            tools=load_tools(TOOLS_VERSION),
            tool_choice="auto",
            temperature=0,
            max_tokens=MAX_TOKENS_PER_TURN,
        )
        msg = from_openai_message(resp.choices[0].message.model_dump())
    except (openai.APIError, IndexError, KeyError, ValueError) as e:
        raise VeriBackendError(f"tool call to model {model} at {base_url} failed: {e!r}") from e
    elapsed = time.monotonic() - start
    if not msg.tool_calls:
        raise VeriBackendError(f"model {model} answered without a tool call: {msg.content!r}")
    raw = [c.name for c in msg.tool_calls if "__raw__" in c.arguments]
    if raw:
        raise VeriBackendError(f"model {model} returned unparseable arguments for {raw}")
    return [f"{c.name}({dumps_args(c.arguments)})" for c in msg.tool_calls], elapsed


# ---------- CLI ----------


def _usd(amount: float | None) -> str:
    return "unknown" if amount is None else f"${amount:.2f}"


def whoami(client: Client, echo: Callable[[str], None] = print) -> None:
    with _veri_call("reading the account"):
        me = client.me()
        balance = client.billing.balance()
        burn = client.billing.burn_rate()
    ws = me.get("workspace") or {}
    echo(f"account:   {me.get('email')}")
    echo(f"workspace: {ws.get('name')} ({ws.get('kind')}, {ws.get('id')})")
    echo(f"balance:   {_usd(balance.get('balance_usd'))}")
    echo(
        f"burn rate: {_usd(burn.get('burn_rate_usd_per_hour'))}/hour, {len(burn.get('active_jobs') or [])} active jobs,"
        f" {len(burn.get('active_deployments') or [])} active deployments"
    )


def create_base(
    client: Client,
    *,
    name: str,
    base_model_hf_id: str,
    scale_to_zero_window_s: int,
    api_key: str,
    api_key_name: str,
    echo: Callable[[str], None] = print,
    check: Callable[[str, str, str], tuple[list[str], float]] = tool_call,
    poll_s: float = DEPLOYMENT_POLL_S,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """Create (or reuse, by name) the multi-adapter deployment, wait until it serves, check a tool call on its base
    alias with api_key (the env variable api_key_name), and print what to register. Registers nothing."""
    with _veri_call("listing deployments"):
        live = [
            d for d in client.deployments.list(limit=100) if d.name == name and d.status not in ("stopped", "failed")
        ]
    if live:
        dep = live[0]
        echo(f"Reusing deployment {dep.id} named {name} ({dep.status}); nothing new is created.")
    else:
        with _veri_call("estimating the serving cost"):
            rate = client.cost.estimate(SERVE_GPU["gpu_type"], SERVE_GPU["gpu_count"], 60, provider=SERVE_PROVIDER)
        echo(
            f"Creating multi-adapter deployment {name}: {base_model_hf_id}, {SERVE_GPU['gpu_type']} x1 on"
            f" {SERVE_PROVIDER}, about {_usd(rate.get('estimated_cost_usd'))}/hour while a replica is up,"
            f" parks after {scale_to_zero_window_s}s idle."
        )
        dep = create_fleet(client, fleet_body(base_model_hf_id, name, scale_to_zero_window_s))
        echo(f"Created deployment {dep.id} ({dep.status}). Waiting until it serves (usually a few minutes).")
        echo("Ctrl+C stops waiting, not the deployment; it parks by itself once idle.")
    start = time.monotonic()
    dep = ensure_serving(client, dep.id, poll_s=poll_s, sleep=sleep)
    echo(f"Serving after {time.monotonic() - start:.0f}s at {_usd(dep.cost_per_hour_usd)}/hour per replica.")

    base_url = endpoint_base_url(client, dep.id)
    echo(f"Checking one tool call on the base alias {dep.name!r} with {api_key_name}.")
    try:
        calls, latency = check(base_url, dep.name, api_key)
    except VeriBackendError as e:
        raise VeriBackendError(
            f"{e}\nThe deployment {dep.id} is up; only the check failed. On a 401, fix {api_key_name}. If the error"
            f" lists the valid model names, the base alias is not the deployment name {dep.name!r}: use the base one"
            f" as --endpoint-model-id for register_base (base URL {base_url})."
        ) from e
    echo(f"Base alias {dep.name!r} answered a tool call in {latency:.1f}s: {', '.join(calls)}")
    echo("")
    echo(f"deployment id:      {dep.id}")
    echo(f"endpoint base URL:  {base_url}")
    echo(f"model id for base:  {dep.name}")
    echo("")
    echo("Next:")
    echo(f"  1. add VERI_DEPLOYMENT_ID={dep.id} to the repo-root .env (TRAINER=veri, wake and smoke read it)")
    if api_key_name != "MODEL_API_KEY":
        echo("  2. set MODEL_API_KEY in .env (the VERI_API_KEY value works); register_base and the agent send it")
    else:
        echo("  2. MODEL_API_KEY works for this deployment; nothing to change")
    echo(
        f"  3. cd train && uv run python -m scar_train.register_base --endpoint-base-url {base_url}"
        f" --endpoint-model-id {dep.name}"
    )
    return dep


def toy_spec_line(task: dict) -> dict:
    """A small valid transcript shaped like an agent run on a seed task (3 assistant turns, so 3 Veri rows), as a
    SPEC 9.8 export line."""
    p = task["params"]
    account, expected = p["account_id"], task["expected"]["number"]
    calls = [
        ToolCall(id="call_1", name="get_accounts", arguments={}),
        ToolCall(
            id="call_2",
            name="get_transactions",
            arguments={"account_id": account, "start_date": p["start_date"], "end_date": p["end_date"]},
        ),
        ToolCall(id="call_3", name="submit_answer", arguments={"number": float(expected)}),
    ]
    results = [
        [{"account_id": account, "name": account.split(":")[-1], "account_type": "expense"}],
        [{"txn_id": "txn-1", "date": p["start_date"], "account_id": account, "amount": expected}],
    ]
    messages = [
        ChatMessage(role="system", content=CHECK_SYSTEM_PROMPT),
        ChatMessage(role="user", content=f"Company: {task['company_id']}\n\n{task['prompt']}"),
    ]
    for i, call in enumerate(calls):
        messages.append(ChatMessage(role="assistant", tool_calls=[call]))
        if i < len(results):
            content = json.dumps(results[i])
            messages.append(ChatMessage(role="tool", tool_call_id=call.id, name=call.name, content=content))
    return {"messages": to_openai_messages(messages), "tools": load_tools(TOOLS_VERSION)}


def smoke_task(path: Path = REPO_ROOT / "fixtures" / "tasks_seed.jsonl") -> dict:
    """The first seed task that asks for a number over one account and date range."""
    for line in path.read_text(encoding="utf-8").splitlines():
        task = json.loads(line)
        if task["expected"]["answer_type"] == "number" and {"account_id", "start_date", "end_date"} <= set(
            task["params"]
        ):
            return task
    raise VeriBackendError(f"no numeric account task in {path}")


def smoke(
    client: Client,
    trainer: VeriTrainer,
    deployer: VeriDeployer,
    *,
    base_model_hf_id: str,
    api_key: str,
    workdir: Path,
    task: dict,
    echo: Callable[[str], None] = print,
    check: Callable[[str, str, str], tuple[list[str], float]] = tool_call,
) -> dict:
    """docs/VERI.md P3-05 smoke steps that need no agent service; returns the measurements it prints."""
    out: dict[str, Any] = {}
    echo("[1/5] account")
    with _veri_call("reading the balance"):
        out["balance_before_usd"] = client.billing.balance().get("balance_usd")
    whoami(client, echo)

    echo(f"[2/5] deployment {deployer.deployment_id}: wake if parked, then one tool call on the base alias")
    start = time.monotonic()
    dep = ensure_serving(client, deployer.deployment_id, poll_s=deployer.wake_poll_s, sleep=deployer.sleep)
    out["wait_serving_s"] = round(time.monotonic() - start, 1)
    calls, out["base_tool_call_s"] = check(deployer.base_url, dep.name, api_key)
    out["deployment_usd_per_hour"] = dep.cost_per_hour_usd
    echo(f"      base alias {dep.name!r}: {', '.join(calls)} in {out['base_tool_call_s']:.1f}s")

    echo(f"[3/5] toy dataset from seed task {task['task_id']} and a managed sft_text job (max_steps {SMOKE_MAX_STEPS})")
    spec_path = workdir / f"{SMOKE_VERSION}.jsonl"
    spec_path.write_text(json.dumps(toy_spec_line(task), ensure_ascii=False) + "\n", encoding="utf-8")
    start = time.monotonic()
    adapter = trainer.train(
        version=SMOKE_VERSION, dataset_path=spec_path, n_examples=1, base_model_hf_id=base_model_hf_id, seed=0
    )
    out["train_and_save_s"] = round(time.monotonic() - start, 1)
    out["training_job_id"], out["adapter_model_id"] = adapter.training_job_id, adapter.adapter_uri
    echo(f"      job {adapter.training_job_id}, adapter model {adapter.adapter_uri}, {out['train_and_save_s']:.0f}s")

    echo(f"[4/5] attach as {SMOKE_VERSION} and wait until active")
    start = time.monotonic()
    base = ModelVersion(
        version="base",
        arm=None,
        seed=None,
        generation=0,
        parent_version=None,
        base_model_hf_id=base_model_hf_id,
        endpoint_base_url=deployer.base_url,
        endpoint_model_id=dep.name,
        adapter_uri=None,
        training_job_id=None,
        snapshot_id=None,
        status="promoted",
        created_at=datetime.now(timezone.utc),
    )
    deployment = deployer.deploy(version=SMOKE_VERSION, adapter=adapter, base=base)
    out["attach_s"] = round(time.monotonic() - start, 1)

    echo(f"[5/5] one tool call on {SMOKE_VERSION}")
    calls, out["adapter_tool_call_s"] = check(deployment.endpoint_base_url, deployment.endpoint_model_id, api_key)
    echo(f"      {SMOKE_VERSION}: {', '.join(calls)} in {out['adapter_tool_call_s']:.1f}s")

    with _veri_call("reading the job and the balance"):
        job = client.training_jobs.get(adapter.training_job_id)
        out["balance_after_usd"] = client.billing.balance().get("balance_usd")
    out.update(
        job_duration_s=job.duration_seconds,
        job_cost_usd=job.cost_usd,
        job_gpu=job.gpu,
        job_provider=job.provider,
    )
    echo("")
    echo("P3-05 smoke passed. Record these in docs/VERI.md (the balance settles with a delay):")
    for key, value in out.items():
        echo(f"  {key}: {value}")
    echo(json.dumps(out, default=str))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scar_train.veri",
        description="Veri backend: check the account, create or wake the deployment, run the P3-05 smoke test.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("whoami", help="print the account, balance and burn rate")
    create = sub.add_parser("create-base", help="create the multi-adapter deployment of BASE_MODEL_HF_ID")
    create.add_argument("--name", default=DEFAULT_DEPLOYMENT_NAME, help="deployment name (default %(default)s)")
    sub.add_parser("wake", help="wake the deployment in VERI_DEPLOYMENT_ID and wait until it serves")
    sub.add_parser("smoke", help="P3-05 smoke test against VERI_DEPLOYMENT_ID (spends credits)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S", stream=sys.stdout)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = get_settings()
    try:
        # Tool calls use the agent's key when it is set, so a wrong MODEL_API_KEY shows up here, not in the loop.
        key_name = "MODEL_API_KEY" if settings.model_api_key else "VERI_API_KEY"
        if args.command == "whoami":
            whoami(make_client())
        elif args.command == "create-base":
            window = int(os.environ.get("VERI_SCALE_TO_ZERO_WINDOW_SECONDS") or DEFAULT_SCALE_TO_ZERO_WINDOW_S)
            create_base(
                make_client(),
                name=args.name,
                base_model_hf_id=_base_model_hf_id(settings),
                scale_to_zero_window_s=window,
                api_key=_require_env(key_name, "the key for the deployment"),
                api_key_name=key_name,
            )
        elif args.command == "wake":
            client = make_client()
            deployment_id = _require_env("VERI_DEPLOYMENT_ID", "the id create-base printed")
            dep = ensure_serving(client, deployment_id)
            print(f"deployment {dep.id} is serving at {endpoint_base_url(client, dep.id)}")
        elif args.command == "smoke":
            trainer, deployer = make_backends(settings)
            trainer.hyperparameters["max_steps"] = SMOKE_MAX_STEPS
            workdir = Path(tempfile.mkdtemp(prefix="scar-veri-smoke-"))
            print(f"working files in {workdir}; tool calls send {key_name}")
            smoke(
                trainer.client,
                trainer,
                deployer,
                base_model_hf_id=_base_model_hf_id(settings),
                api_key=_require_env(key_name, "the key for the deployment"),
                workdir=workdir,
                task=smoke_task(),
            )
    except VeriBackendError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
