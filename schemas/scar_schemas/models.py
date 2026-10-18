# scar_schemas/models.py — SHARED CONTRACT, schema version 1.0.0
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

Arm = Literal["A", "B", "C", "D", "E"]
Purpose = Literal["traffic", "sampling", "eval", "regression", "judge", "gepa", "smoke"]
StopReason = Literal["submitted", "max_steps", "no_submit", "model_error", "tool_error", "timeout"]


class Split(str, Enum):
    train = "train"
    eval = "eval"


class TaskFamily(str, Enum):
    aggregation = "aggregation"
    anomaly = "anomaly"
    bookkeeping = "bookkeeping"


class Origin(str, Enum):
    seed = "seed"            # hand-written fixtures
    generated = "generated"  # template generator (train pool)
    variant = "variant"      # mutated from an included failure
    judge = "judge"          # created from the judge UI
    eval = "eval"            # frozen eval set


class AnswerType(str, Enum):
    number = "number"                # one Decimal, compared with tolerance
    id_set = "id_set"                # set of ledger IDs, exact set match
    journal_entry = "journal_entry"  # sandbox entries written during the run


# ---------- tasks ----------

class JournalLine(BaseModel):
    account_id: str
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    memo: str | None = None


class JournalEntrySpec(BaseModel):
    entry_date: str              # YYYY-MM-DD
    lines: list[JournalLine]


class ExpectedAnswer(BaseModel):
    answer_type: AnswerType
    number: Decimal | None = None
    tolerance: Decimal = Decimal("0.01")
    id_set: list[str] | None = None
    journal_entries: list[JournalEntrySpec] | None = None


class Task(BaseModel):
    task_id: str
    company_id: str
    split: Split
    family: TaskFamily
    template_id: str
    params: dict[str, Any]
    prompt: str                  # shown to the agent
    expected: ExpectedAnswer     # NEVER shown to the agent or sent to any LLM
    difficulty: int = Field(ge=1, le=3)
    origin: Origin
    parent_failure_id: str | None = None
    created_at: datetime


class TemplateSpec(BaseModel):
    template_id: str             # e.g. "agg.total_by_account_period"
    family: TaskFamily
    answer_type: AnswerType
    description: str             # one sentence; also used as a Jev Choice criterion
    params_schema: dict[str, Any]  # JSON Schema for params
    phrasings: list[str]         # >= 5 question templates with {param} placeholders
    difficulty: int = Field(ge=1, le=3)


# ---------- transcripts ----------

class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]    # parsed JSON; unparseable args stored as {"__raw__": "<string>"}


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[ToolCall] | None = None   # assistant only
    tool_call_id: str | None = None            # tool only
    name: str | None = None                    # tool only: tool name


# ---------- runs ----------

class RunRequest(BaseModel):
    task_id: str
    model_version: str                     # key into app.model_versions
    prompt_version: str = "system_v1"      # key: "system_v1" (file) or app.prompt_versions
    arm: Arm = "D"
    seed: int = 0
    generation: int = 0
    purpose: Purpose
    temperature: float = 0.0
    n: int = Field(default=1, ge=1, le=16) # independent attempts
    max_steps: int = 12


class RunBatchRequest(BaseModel):
    requests: list[RunRequest]
    max_concurrency: int = Field(default=8, ge=1, le=64)


class RunResult(BaseModel):
    run_id: str
    task_id: str
    company_id: str
    split: Split
    family: TaskFamily
    model_version: str
    endpoint_model_id: str
    prompt_version: str
    tools_version: str
    arm: Arm
    seed: int
    generation: int
    purpose: Purpose
    origin: Origin
    attempt_index: int
    temperature: float
    messages: list[ChatMessage]            # exactly as sent to / received from the model
    final_answer: dict[str, Any] | None    # arguments of submit_answer, or None
    stop_reason: StopReason
    passed: bool
    verify_detail: str
    steps: int
    input_tokens: int
    output_tokens: int
    latency_ms: int
    sentry_trace_id: str | None
    created_at: datetime


# ---------- env: tools and verification ----------

class ToolExecuteRequest(BaseModel):
    run_id: str
    company_id: str
    name: str
    arguments: dict[str, Any]


class ToolExecuteResult(BaseModel):
    ok: bool
    result: Any | None = None    # JSON-serializable
    error: str | None = None     # message for the model when ok is False


class VerifyRequest(BaseModel):
    task_id: str
    run_id: str
    stop_reason: StopReason
    final_answer: dict[str, Any] | None


class VerifyResult(BaseModel):
    passed: bool
    detail: str                  # e.g. "number: expected 1234.50, got 1243.50"


# ---------- failures ----------

class FailureCause(str, Enum):
    wrong_tool = "wrong_tool"
    bad_tool_args = "bad_tool_args"
    reasoning_error = "reasoning_error"
    wrong_final_format = "wrong_final_format"
    gave_up = "gave_up"
    environment_error = "environment_error"
    ambiguous_task = "ambiguous_task"
    other = "other"


class FailureRecord(BaseModel):
    failure_id: str
    run_id: str
    task_id: str
    arm: Arm
    seed: int
    generation: int
    model_version: str
    origin: Origin
    cause: FailureCause
    cause_confidence: float
    cause_probabilities: dict[str, float]
    learnable_p: float
    included: bool
    exclusion_reason: str | None
    jev_model: str
    created_at: datetime


class MineRequest(BaseModel):
    arm: Arm
    seed: int
    generation: int
    model_version: str
    prompt_version: str


# ---------- env: generation, mutation, traffic ----------

class GenerateRequest(BaseModel):
    split: Split
    n: int
    seed: int
    origin: Origin               # generated or eval
    family: TaskFamily | None = None


class FromParamsRequest(BaseModel):
    template_id: str
    company_id: str
    params: dict[str, Any]
    origin: Origin               # judge
    prompt: str | None = None    # judge's own wording; if None a phrasing is used
    dry_run: bool = False        # True: validate and describe, do not store


class FromParamsResult(BaseModel):
    task: Task | None            # None when dry_run is True
    interpretation: str          # canonical one-sentence restatement, no answer included
    valid: bool
    error: str | None = None


class MutateRequest(BaseModel):
    failure_ids: list[str]
    variants_per_failure: int = 10
    seed: int


class SimulateRequest(BaseModel):
    arm: Arm
    seed: int
    generation: int
    model_version: str
    prompt_version: str
    n_tasks: int = 200


class SimulateResult(BaseModel):
    run_ids: list[str]
    passed: int
    failed: int


class EvalSet(BaseModel):
    eval_set_id: str
    task_ids: list[str]          # sorted
    sha256: str                  # sha256 of "\n".join(sorted task_ids)


# ---------- prompts and learn-steps (arms B, C) ----------

class PromptVersion(BaseModel):
    prompt_version: str
    arm: Arm
    seed: int
    generation: int
    text: str
    parent_prompt_version: str | None
    is_candidate: bool = False   # True for GEPA intermediate candidates
    created_at: datetime


class LearnRequest(BaseModel):
    arm: Literal["B", "C"]
    seed: int
    generation: int
    model_version: str
    current_prompt_version: str
    failure_ids: list[str]       # included failures for this generation


class LearnResult(BaseModel):
    prompt_version: str          # new candidate prompt (stored in app.prompt_versions)
    notes: str


# ---------- training and registry ----------

class TrainingExample(BaseModel):
    example_id: str
    snapshot_id: str
    lineage: str                 # f"{arm}-s{seed}", e.g. "D-s0"
    task_id: str
    source: Literal["self", "teacher", "replay"]
    origin_run_id: str
    prompt_version: str
    tools_version: str
    messages: list[ChatMessage]  # verbatim from the passed RunResult
    created_at: datetime


class ModelVersion(BaseModel):
    version: str                 # "base" or f"{arm}-s{seed}-g{generation}"
    arm: Arm | None              # None for base
    seed: int | None
    generation: int
    parent_version: str | None
    base_model_hf_id: str
    endpoint_base_url: str
    endpoint_model_id: str       # value sent as `model` in chat.completions
    adapter_uri: str | None
    training_job_id: str | None
    snapshot_id: str | None
    status: Literal["candidate", "promoted", "rejected", "failed"]
    created_at: datetime


class GenStatus(str, Enum):
    collecting = "collecting"
    mining = "mining"
    mutating = "mutating"
    building_data = "building_data"
    training = "training"
    deploying = "deploying"
    learning_prompt = "learning_prompt"   # arms B, C, E
    gating = "gating"
    promoted = "promoted"
    rejected = "rejected"
    failed = "failed"


class GenerationRecord(BaseModel):
    generation_id: str           # f"{arm}-s{seed}-g{generation}"
    arm: Arm
    seed: int
    generation: int
    status: GenStatus
    parent_model_version: str
    parent_prompt_version: str
    candidate_model_version: str | None
    candidate_prompt_version: str | None
    current_model_version: str   # in use after this generation finishes
    current_prompt_version: str
    traffic_runs: int = 0
    failures_mined: int = 0
    failures_included: int = 0
    variants_created: int = 0
    examples_built: int = 0
    eval_score: float | None = None        # candidate's score on eval_v1
    parent_eval_score: float | None = None
    regression_score: float | None = None
    parent_regression_score: float | None = None  # parent re-run on the same regression set (1.3.0)
    notes: str = ""
    started_at: datetime
    finished_at: datetime | None = None


# ---------- judge UI ----------

class JudgeParseRequest(BaseModel):
    company_id: str              # must be a train-split company
    question: str


class JudgeParseResult(BaseModel):
    template_id: str | None
    template_confidence: float
    params: dict[str, Any]
    interpretation: str          # shown to the judge to confirm
    valid: bool
    error: str | None = None


class JudgeRunRequest(BaseModel):
    company_id: str
    template_id: str
    params: dict[str, Any]
    question: str


class JudgeRunResult(BaseModel):
    task_id: str
    run: RunResult
    sentry_trace_url: str | None
