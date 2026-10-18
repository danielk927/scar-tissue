"""Sentry instrumentation (SPEC 8.8). Verified API usage and sources: docs/SENTRY.md."""

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import sentry_sdk
from scar_schemas.models import RunRequest, RunResult, Task
from sentry_sdk.consts import OP, SPANDATA
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.openai import OpenAIIntegration

from .config import SERVICE_NAME, env

AGENT_NAME = "ledger-agent"
FAILURE_EVENT_PURPOSES = ("traffic", "judge")

log = logging.getLogger("scar_agent.runs")

# Set by the HTTP layer for judge requests so the run joins the browser's trace (docs/SENTRY.md section 9).
incoming_trace_headers: ContextVar[dict[str, str] | None] = ContextVar("incoming_trace_headers", default=None)

_enabled = False


def init_sentry() -> None:
    global _enabled
    dsn = env("SENTRY_DSN")
    if not dsn:
        logging.getLogger("scar_agent").warning("SENTRY_DSN not set; Sentry disabled")
        return
    sentry_sdk.init(
        dsn=dsn,
        environment=env("SENTRY_ENVIRONMENT", "hackathon"),
        traces_sample_rate=1.0,
        send_default_pii=True,
        integrations=[LoggingIntegration(capture_sentry_logs=True, sentry_logs_level=logging.INFO,
                                         level=logging.INFO, event_level=logging.ERROR)],
        # its automatic gen_ai.chat spans would duplicate ours
        disabled_integrations=[OpenAIIntegration()],
    )
    scope = sentry_sdk.get_global_scope()
    scope.set_tag("service", SERVICE_NAME)
    scope.set_attribute("service", SERVICE_NAME)
    _enabled = True


def trace_url(trace_id: str | None) -> str | None:
    org = env("SENTRY_ORG")
    if not trace_id or not org:
        return None
    # format confirmed by opening a real trace link (docs/SENTRY.md section 10)
    return f"https://{org}.sentry.io/explore/traces/trace/{trace_id}/"


def _j(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def to_sentry_messages(messages: list[dict]) -> list[dict]:
    """OpenAI-format messages -> Sentry's {role, parts} shape (docs/SENTRY.md section 3.2)."""
    out = []
    for m in messages:
        parts: list[dict] = []
        if m["role"] == "tool":
            parts.append({"type": "tool_call_response", "id": m.get("tool_call_id"),
                          "name": m.get("name"), "content": m.get("content")})
        else:
            if m.get("content"):
                parts.append({"type": "text", "content": m["content"]})
            for c in m.get("tool_calls") or []:
                raw = c["function"]["arguments"]
                try:
                    args = json.loads(raw)
                except json.JSONDecodeError:
                    args = raw
                parts.append({"type": "tool_call", "id": c["id"], "name": c["function"]["name"],
                              "arguments": args})
        out.append({"role": m["role"], "parts": parts})
    return out


class SpanHandle:
    def __init__(self, span: Any):
        self.span = span

    def error(self, message: str) -> None:
        self.span.set_status("internal_error")
        self.span.set_data("error.message", message)


class ChatSpan(SpanHandle):
    def finish_ok(self, output_message: dict, input_tokens: int, output_tokens: int,
                  response_model: str | None) -> None:
        self.span.set_data(SPANDATA.GEN_AI_OUTPUT_MESSAGES, _j(to_sentry_messages([output_message])))
        self.span.set_data(SPANDATA.GEN_AI_USAGE_INPUT_TOKENS, input_tokens)
        self.span.set_data(SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS, output_tokens)
        self.span.set_data(SPANDATA.GEN_AI_USAGE_TOTAL_TOKENS, input_tokens + output_tokens)
        if response_model:
            self.span.set_data(SPANDATA.GEN_AI_RESPONSE_MODEL, response_model)


class ToolSpan(SpanHandle):
    def finish_ok(self, content: str) -> None:
        self.span.set_data(SPANDATA.GEN_AI_TOOL_CALL_RESULT, content)
        if content.startswith('{"error"'):
            self.span.set_status("internal_error")


class RunTrace:
    def __init__(self, scope: Any, root: Any, purpose: str):
        self.scope, self.root, self.purpose = scope, root, purpose
        self.trace_id: str | None = root.trace_id if _enabled else None

    @contextmanager
    def chat(self, model_id: str, messages: list[dict]) -> Iterator[ChatSpan]:
        with sentry_sdk.start_span(op=OP.GEN_AI_CHAT, name=f"chat {model_id}") as span:
            span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "chat")
            span.set_data(SPANDATA.GEN_AI_PROVIDER_NAME, "openai")  # OpenAI-compatible endpoint
            span.set_data(SPANDATA.GEN_AI_AGENT_NAME, AGENT_NAME)
            span.set_data(SPANDATA.GEN_AI_REQUEST_MODEL, model_id)
            span.set_data(SPANDATA.GEN_AI_INPUT_MESSAGES, _j(to_sentry_messages(messages)))
            yield ChatSpan(span)

    @contextmanager
    def tool(self, name: str, arguments: dict) -> Iterator[ToolSpan]:
        with sentry_sdk.start_span(op=OP.GEN_AI_EXECUTE_TOOL, name=f"execute_tool {name}") as span:
            span.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "execute_tool")
            span.set_data(SPANDATA.GEN_AI_AGENT_NAME, AGENT_NAME)
            span.set_data(SPANDATA.GEN_AI_TOOL_NAME, name)
            span.set_data(SPANDATA.GEN_AI_TOOL_CALL_ARGUMENTS, _j(arguments))
            yield ToolSpan(span)

    def finish(self, result: RunResult, template_id: str) -> None:
        """Called inside the run transaction, so the event and log link to this run's trace."""
        end = {"passed": str(result.passed).lower(), "stop_reason": result.stop_reason}
        self.scope.set_tags(end)
        self.scope.set_attributes(end)
        self.root.set_status("ok" if result.passed else "internal_error")
        if not result.passed and result.purpose in FAILURE_EVENT_PURPOSES:
            sentry_sdk.capture_message("agent_run_failed", level="warning",
                                       fingerprint=["agent_run_failed", template_id, result.stop_reason])
        log.info("run finished: passed=%s stop_reason=%s steps=%d", result.passed, result.stop_reason,
                 result.steps, extra={
                     "run_id": result.run_id, "task_id": result.task_id, "arm": result.arm,
                     "seed": result.seed, "generation": result.generation,
                     "model_version": result.model_version})


@contextmanager
def run_trace(run_id: str, req: RunRequest, task: Task) -> Iterator[RunTrace]:
    """One isolation scope and one transaction per run, so concurrent runs never share tags."""
    with sentry_sdk.isolation_scope() as scope:
        tags = {
            "run_id": run_id, "task_id": task.task_id, "arm": req.arm, "seed": str(req.seed),
            "generation": str(req.generation), "purpose": req.purpose, "origin": task.origin.value,
            "split": task.split.value, "family": task.family.value, "template_id": task.template_id,
            "model_version": req.model_version, "prompt_version": req.prompt_version,
        }
        scope.set_tags(tags)
        scope.set_attributes(tags)  # tags do not reach Sentry Logs; attributes do
        sentry_sdk.ai.set_conversation_id(run_id)

        name = f"invoke_agent {AGENT_NAME}"
        headers = incoming_trace_headers.get() if req.purpose == "judge" else None
        if headers:
            root_cm = sentry_sdk.start_transaction(
                sentry_sdk.continue_trace(headers, op=OP.GEN_AI_INVOKE_AGENT, name=name))
        else:
            root_cm = sentry_sdk.start_transaction(op=OP.GEN_AI_INVOKE_AGENT, name=name)
        with root_cm as root:
            root.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "invoke_agent")
            root.set_data(SPANDATA.GEN_AI_AGENT_NAME, AGENT_NAME)
            yield RunTrace(scope, root, req.purpose)
