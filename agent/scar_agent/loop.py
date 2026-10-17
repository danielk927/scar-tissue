"""The agent loop, exactly per SPEC 9.2. One call = one attempt = one RunResult."""

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import openai
from scar_schemas.constants import MAX_TOKENS_PER_TURN, TOOL_RESULT_MAX_CHARS, TOOLS_VERSION
from scar_schemas.ids import new_id
from scar_schemas.models import (
    ChatMessage,
    RunRequest,
    RunResult,
    Task,
    ToolExecuteRequest,
    VerifyRequest,
)
from scar_schemas.openai_format import dumps_args, from_openai_message, load_tools, to_openai_messages

from . import observability as obs
from .clients import MODEL_TIMEOUT_S, EnvClient, ToolInfraError

RUN_TIMEOUT_S = 180.0
NUDGE = "Call a tool, or call submit_answer to finish."
SUBMIT_OK = '{"ok":true}'
RAW_ARGS_ERROR = '{"error":"arguments were not valid JSON"}'


@dataclass
class AttemptContext:
    """Everything an attempt needs that is resolved once per RunRequest."""

    req: RunRequest
    task: Task
    company_name: str
    prompt_text: str
    endpoint_model_id: str
    llm: Any                     # AsyncOpenAI-compatible: .chat.completions.create(...)
    env: EnvClient


def user_message(company_name: str, task: Task) -> str:
    return f"Company: {company_name}\n\n{task.prompt}"


def truncate(content: str) -> str:
    if len(content) > TOOL_RESULT_MAX_CHARS:
        return content[:TOOL_RESULT_MAX_CHARS] + "...[truncated]"
    return content


class _Loop:
    """Mutable state of one attempt, so a whole-run timeout keeps what was done so far."""

    def __init__(self, ctx: AttemptContext, run_id: str, trace: "obs.RunTrace"):
        self.ctx, self.run_id, self.trace = ctx, run_id, trace
        self.messages: list[ChatMessage] = [
            ChatMessage(role="system", content=ctx.prompt_text),
            ChatMessage(role="user", content=user_message(ctx.company_name, ctx.task)),
        ]
        self.final_answer: dict[str, Any] | None = None
        self.stop_reason: str | None = None
        self.steps = 0
        self.input_tokens = 0
        self.output_tokens = 0

    async def run(self) -> None:
        ctx = self.ctx
        tools = load_tools(TOOLS_VERSION)
        no_tool_turns = 0
        for step in range(1, ctx.req.max_steps + 1):
            self.steps = step
            sent = to_openai_messages(self.messages)
            with self.trace.chat(ctx.endpoint_model_id, sent) as chat_span:
                try:
                    async with asyncio.timeout(MODEL_TIMEOUT_S):
                        resp = await ctx.llm.chat.completions.create(
                            model=ctx.endpoint_model_id,
                            messages=sent,
                            tools=tools,
                            tool_choice="auto",
                            temperature=ctx.req.temperature,
                            max_tokens=MAX_TOKENS_PER_TURN,
                        )
                    msg = from_openai_message(resp.choices[0].message.model_dump())
                except (TimeoutError, openai.APIError) as e:
                    chat_span.error(repr(e))
                    self.stop_reason = "model_error"
                    break
                except Exception as e:  # malformed response (no choices, bad role, ...)
                    chat_span.error(f"malformed model response: {e!r}")
                    self.stop_reason = "model_error"
                    break
                usage = getattr(resp, "usage", None)
                in_tok = (usage.prompt_tokens or 0) if usage else 0
                out_tok = (usage.completion_tokens or 0) if usage else 0
                chat_span.finish_ok(to_openai_messages([msg])[0], in_tok, out_tok,
                                    getattr(resp, "model", None))
            self.messages.append(msg)
            self.input_tokens += in_tok
            self.output_tokens += out_tok

            if not msg.tool_calls:
                no_tool_turns += 1
                if no_tool_turns == 1:
                    self.messages.append(ChatMessage(role="user", content=NUDGE))
                    continue
                self.stop_reason = "no_submit"
                break

            for call in msg.tool_calls:
                if call.name == "submit_answer":
                    self.final_answer = call.arguments
                    self.messages.append(ChatMessage(role="tool", tool_call_id=call.id,
                                                     name="submit_answer", content=SUBMIT_OK))
                    self.stop_reason = "submitted"
                    break  # ignore any later calls in this message
                with self.trace.tool(call.name, call.arguments) as tool_span:
                    if "__raw__" in call.arguments:
                        content = RAW_ARGS_ERROR
                    else:
                        try:
                            r = await ctx.env.execute_tool(ToolExecuteRequest(
                                run_id=self.run_id, company_id=ctx.task.company_id,
                                name=call.name, arguments=call.arguments))
                        except ToolInfraError as e:
                            tool_span.error(str(e))
                            self.stop_reason = "tool_error"
                            break
                        content = dumps_args(r.result) if r.ok else dumps_args({"error": r.error})
                    content = truncate(content)
                    tool_span.finish_ok(content)
                self.messages.append(ChatMessage(role="tool", tool_call_id=call.id,
                                                 name=call.name, content=content))
            if self.stop_reason:
                break
        if self.stop_reason is None:
            self.stop_reason = "max_steps"


async def run_attempt(ctx: AttemptContext, attempt_index: int) -> RunResult:
    """Run one attempt and verify it. Does not store; the caller stores."""
    run_id = new_id("run")
    started = time.monotonic()
    with obs.run_trace(run_id, ctx.req, ctx.task) as trace:
        loop = _Loop(ctx, run_id, trace)
        try:
            async with asyncio.timeout(RUN_TIMEOUT_S):
                await loop.run()
        except TimeoutError:
            loop.stop_reason = "timeout"

        verify = await ctx.env.verify(VerifyRequest(
            task_id=ctx.task.task_id, run_id=run_id,
            stop_reason=loop.stop_reason, final_answer=loop.final_answer))

        result = RunResult(
            run_id=run_id,
            task_id=ctx.task.task_id,
            company_id=ctx.task.company_id,
            split=ctx.task.split,
            family=ctx.task.family,
            model_version=ctx.req.model_version,
            endpoint_model_id=ctx.endpoint_model_id,
            prompt_version=ctx.req.prompt_version,
            tools_version=TOOLS_VERSION,
            arm=ctx.req.arm,
            seed=ctx.req.seed,
            generation=ctx.req.generation,
            purpose=ctx.req.purpose,
            origin=ctx.task.origin,
            attempt_index=attempt_index,
            temperature=ctx.req.temperature,
            messages=loop.messages,
            final_answer=loop.final_answer,
            stop_reason=loop.stop_reason,
            passed=verify.passed,
            verify_detail=verify.detail,
            steps=loop.steps,
            input_tokens=loop.input_tokens,
            output_tokens=loop.output_tokens,
            latency_ms=int((time.monotonic() - started) * 1000),
            sentry_trace_id=trace.trace_id,
            created_at=datetime.now(timezone.utc),
        )
        trace.finish(result, ctx.task.template_id)
    return result


def summarize(result: RunResult) -> str:
    return json.dumps({"run_id": result.run_id, "passed": result.passed,
                       "stop_reason": result.stop_reason, "steps": result.steps})
