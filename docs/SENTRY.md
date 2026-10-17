# Sentry: verified setup notes (P2, F13)

Verified 2026-10-17. Every fact is tagged:

- **(source: `<file>`)** means read in the installed `sentry-sdk` 2.72.0 (paths are relative to `site-packages/`) or the `@sentry/nextjs` 11.8.0 npm tarball.
- **(probe)** means observed by running a script against the installed SDK with a capturing transport (`/tmp/.../scratchpad/sentry/probe*.py`, FastAPI `TestClient`, nothing sent to Sentry).
- **(docs: URL)** means official Sentry docs.

Anything not covered by one of these is marked **UNKNOWN**.

## 0. What we actually ship (read this first)

- **Agent service:** `agent/scar_agent/observability.py` implements sections 2–6: one isolation scope and one `start_transaction` per run, chat and tool spans with the section 3.2 keys, messages converted to `{role, parts}` (part types verified on the manual-instrumentation docs page: `text`, `tool_call` {id, name, arguments}, `tool_call_response` {id, name, content}), `agent_run_failed` events inside the run transaction, `OpenAIIntegration` disabled, Logs through `LoggingIntegration(capture_sentry_logs=True)`. `agent/tests/test_observability.py` checks all of it against a capturing transport. With no `SENTRY_DSN`, Sentry is skipped and `sentry_trace_id` stays null.
- **Judge trace linking:** runs with `purpose="judge"` (from `/judge/run` or `/agent/run`) continue the incoming `sentry-trace`/`baggage`; all other runs start a fresh trace.
- **`send_default_pii` is deprecated in 2.72.0** (DeprecationWarning: "Use data_collection instead"). It still works, so we keep the spec's option; revisit only if it stops working.
- **Web:** `@sentry/nextjs` pinned to **11.4.0** (newest release at least two weeks old; `withSentryConfig` from `@sentry/nextjs/config`, `captureRouterTransitionStart`, `captureRequestError` and `replayIntegration` exports checked in the installed package). The browser calls the services through Next.js rewrites (`/api/agent/*`, `/api/train/*`, `/api/env/*`), so requests are same-origin and the browser's trace headers pass through to FastAPI.
- **Session Replay rate:** `NEXT_PUBLIC_SENTRY_REPLAY_RATE` (default 0, set to 1 for the demo) because of the 50-replay free quota (section 11). Error replays stay at 100%.
- **Persons 1 and 3:** paste the section 6 snippet into your service to get Sentry Logs and tracing (P2-10).

## 1. SDK versions

| Package | Version | How checked |
|---|---|---|
| `sentry-sdk` (Python) | **2.72.0** | `uv pip install sentry-sdk fastapi` on Python 3.12.3 |
| `fastapi` | 0.143.3 | same install |
| `openai` (only for the auto-instrumentation probe) | 3.34.0 | same venv |
| `@sentry/nextjs` | **11.8.0** (`latest` dist-tag; v10 line is 10.76.2) | `npm view @sentry/nextjs dist-tags` |
| `@sentry/nextjs` peer `next` | `^14.0 \|\| ^15.0.0-rc.0 \|\| ^16.0.0-0`; Node `>=20.19.0 <22 \|\| >=22.12.0 <23 \|\| >=23.2.0` | `npm view` |

Pin `sentry-sdk>=2.64.0`. From 2.64.0, gen_ai spans are sent as standalone span items, and the AI Conversations view needs that (docs: https://docs.sentry.io/platforms/python/tracing/instrumentation/custom-instrumentation/ai-agents-module/).

## 2. Python init (every service)

```python
import logging
import os

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

def init_sentry(service: str, *, disable_openai_autoinstrument: bool = False) -> None:
    disabled = []
    if disable_openai_autoinstrument:
        from sentry_sdk.integrations.openai import OpenAIIntegration
        disabled.append(OpenAIIntegration())

    sentry_sdk.init(
        dsn=os.environ["SENTRY_DSN"],
        environment=os.environ.get("SENTRY_ENVIRONMENT", "hackathon"),
        traces_sample_rate=1.0,
        send_default_pii=True,  # needed for prompt/response/tool content on gen_ai spans
        integrations=[
            LoggingIntegration(
                capture_sentry_logs=True,        # stdlib logging -> Sentry Logs
                sentry_logs_level=logging.INFO,
                level=logging.INFO,              # breadcrumbs
                event_level=logging.ERROR,       # ERROR logs also become issues
            ),
        ],
        disabled_integrations=disabled,
    )
    # Service-wide values go on the GLOBAL scope so every request/task inherits them.
    gs = sentry_sdk.get_global_scope()
    gs.set_tag("service", service)        # events + transactions
    gs.set_attribute("service", service)  # logs (attributes-based telemetry)
```

- `init` keyword arguments `dsn`, `environment`, `traces_sample_rate`, `send_default_pii`, `integrations`, `disabled_integrations`, `default_integrations`, `auto_enabling_integrations`, `trace_propagation_targets`, `enable_logs`, `before_send_log`, `stream_gen_ai_spans` (default `True`), and `trace_lifecycle` (`"static"`/`"stream"`) all exist (source: `sentry_sdk/consts.py` `ClientConstructor.__init__`, lines ~1389-1454).
- The final merged scope always starts from a copy of the global scope, then applies the isolation scope and the current scope, including `_attributes` and `_tags` (source: `sentry_sdk/scope.py` `_merge_scopes`, `update_from_scope`). That is why service-wide tags go on the global scope.
- `send_default_pii=True` is required for prompt and response content on AI spans (docs: https://docs.sentry.io/platforms/python/tracing/instrumentation/custom-instrumentation/ai-agents-module/).

## 3. Agent spans

### 3.1 Which tracing API

SDK 2.72 has two modes (source: `sentry_sdk/traces.py` module docstring, `sentry_sdk/tracing_utils.py:has_span_streaming_enabled`):

- **Static (the default, and the one we use):** `sentry_sdk.start_transaction(op=, name=)` starts the root, `sentry_sdk.start_span(op=, name=)` starts children, and `span.set_data(key, value)` sets attributes.
- **Stream (`trace_lifecycle="stream"`):** `sentry_sdk.traces.start_span(name=, attributes=)` and `span.set_attribute()`. In stream mode, `sentry_sdk.start_span` returns a `NoOpSpan` with a DeprecationWarning. In static mode, `sentry_sdk.traces.start_span` returns a `NoOpStreamedSpan` (source: `sentry_sdk/scope.py:Scope.start_span`, `sentry_sdk/traces.py:start_span`). **Do not mix the two.**

In static mode, `start_span` with no active span creates a parentless `Span` that is never sent: "Only spans contained in a transaction are sent to Sentry" (source: `sentry_sdk/scope.py` `Scope.start_span` docstring). **The root of each run must therefore be `start_transaction`.** The AI-agents docs examples show only `start_span(...)`, because they assume a transaction already exists (docs: https://docs.sentry.io/platforms/python/agent-tracing/manual-instrumentation/).

Static spans have `set_data(key, value)`, `update_data(dict)`, `set_tag`, and `set_status`. There is no `set_attribute`, and the constructor has no `attributes=` kwarg (source: `sentry_sdk/tracing.py` `Span.__init__`, `Span.set_data`).

**Values.** The docs say: "Complex values (messages, tool definitions, arrays) must be **JSON strings**" (docs: https://docs.sentry.io/platforms/python/agent-tracing/manual-instrumentation/). In the probe, a raw `list[dict]` passed to `set_data` was stringified by the SDK (Python repr/JSON-ish), not dropped. Still, always `json.dumps` it yourself. Ints stay ints (`"type": "integer"`) (probe).

### 3.2 Attribute key constants (`sentry_sdk.consts.SPANDATA` / `OP`)

All from source: `sentry_sdk/consts.py`.

| Purpose | Constant | Key | Note |
|---|---|---|---|
| op name | `SPANDATA.GEN_AI_OPERATION_NAME` | `gen_ai.operation.name` | |
| agent name | `GEN_AI_AGENT_NAME` | `gen_ai.agent.name` | |
| conversation | `GEN_AI_CONVERSATION_ID` | `gen_ai.conversation.id` | see 3.4 |
| model requested | `GEN_AI_REQUEST_MODEL` | `gen_ai.request.model` | |
| model responded | `GEN_AI_RESPONSE_MODEL` | `gen_ai.response.model` | docs list it as required on chat |
| provider | `GEN_AI_PROVIDER_NAME` | `gen_ai.provider.name` | replaces deprecated `gen_ai.system`; docs list it as required on chat |
| input messages | `GEN_AI_INPUT_MESSAGES` | `gen_ai.input.messages` | replaces deprecated `gen_ai.request.messages` |
| output messages | `GEN_AI_OUTPUT_MESSAGES` | `gen_ai.output.messages` | replaces deprecated `gen_ai.response.text` and `gen_ai.response.tool_calls` |
| system prompt | `GEN_AI_SYSTEM_INSTRUCTIONS` | `gen_ai.system_instructions` | |
| tool definitions | `GEN_AI_TOOL_DEFINITIONS` | `gen_ai.tool.definitions` | replaces deprecated `gen_ai.request.available_tools` |
| finish reasons | `GEN_AI_RESPONSE_FINISH_REASONS` | `gen_ai.response.finish_reasons` | |
| tokens in | `GEN_AI_USAGE_INPUT_TOKENS` | `gen_ai.usage.input_tokens` | |
| tokens out | `GEN_AI_USAGE_OUTPUT_TOKENS` | `gen_ai.usage.output_tokens` | |
| tokens total | `GEN_AI_USAGE_TOTAL_TOKENS` | `gen_ai.usage.total_tokens` | |
| tool name | `GEN_AI_TOOL_NAME` | `gen_ai.tool.name` | |
| **tool input** | `GEN_AI_TOOL_CALL_ARGUMENTS` | **`gen_ai.tool.call.arguments`** | `gen_ai.tool.input` is **deprecated** |
| **tool output** | `GEN_AI_TOOL_CALL_RESULT` | **`gen_ai.tool.call.result`** | `gen_ai.tool.output` is **deprecated** |
| tool description | `GEN_AI_TOOL_DESCRIPTION` | `gen_ai.tool.description` | |
| ops | `OP.GEN_AI_INVOKE_AGENT`, `OP.GEN_AI_CHAT`, `OP.GEN_AI_EXECUTE_TOOL` | `gen_ai.invoke_agent`, `gen_ai.chat`, `gen_ai.execute_tool` | |

The docs agree on the tool keys: `gen_ai.tool.call.arguments` and `gen_ai.tool.call.result`, with `gen_ai.tool.input`/`gen_ai.tool.output` deprecated. `gen_ai.output.messages` is recommended and `gen_ai.response.text` is deprecated (docs: https://docs.sentry.io/platforms/python/agent-tracing/manual-instrumentation/).

**Message JSON shape (docs):** `'[{"role": "user", "parts": [{"type": "text", "content": "..."}]}]'`, with role in `user|assistant|tool|system`. `gen_ai.output.messages` uses the same shape (docs: https://docs.sentry.io/platforms/python/agent-tracing/manual-instrumentation/). The exact `parts` types for tool calls and tool results are **UNKNOWN** here; read that page before writing the converter. Sending OpenAI-format messages as a JSON string is accepted by the SDK (probe), but whether the Sentry UI renders them nicely is **UNKNOWN**.

### 3.3 Code pattern

```python
import json
import sentry_sdk
from sentry_sdk.consts import OP, SPANDATA

AGENT = "ledger-agent"

async def run_one(task, ctx, parent_headers: dict | None = None) -> RunResult:
    # Each concurrent run: own isolation scope -> own tags, own transaction, own trace.
    with sentry_sdk.isolation_scope() as scope:
        tags = {"run_id": ctx.run_id, "task_id": task.task_id, "arm": ctx.arm, "seed": str(ctx.seed),
                "generation": str(ctx.generation), "purpose": ctx.purpose, "origin": ctx.origin,
                "split": task.split, "family": task.family, "template_id": task.template_id,
                "model_version": ctx.model_version, "prompt_version": ctx.prompt_version}
        scope.set_tags(tags)               # events + transaction tags
        scope.set_attributes(tags)         # Sentry Logs attributes (filter logs by run_id)
        sentry_sdk.ai.set_conversation_id(ctx.run_id)  # gen_ai.conversation.id on every gen_ai span

        if parent_headers:   # judge /ask: join the browser's trace (see section 9)
            # parent_headers = {"sentry-trace": ..., "baggage": ...} captured in the endpoint
            root_cm = sentry_sdk.start_transaction(sentry_sdk.continue_trace(
                parent_headers, op=OP.GEN_AI_INVOKE_AGENT, name=f"invoke_agent {AGENT}"))
        else:                # traffic batch: fresh trace per run
            root_cm = sentry_sdk.start_transaction(op=OP.GEN_AI_INVOKE_AGENT, name=f"invoke_agent {AGENT}")
        with root_cm as root:
            root.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "invoke_agent")
            root.set_data(SPANDATA.GEN_AI_AGENT_NAME, AGENT)
            trace_id = root.trace_id       # store as RunResult.sentry_trace_id

            # ---- each model call ----
            with sentry_sdk.start_span(op=OP.GEN_AI_CHAT, name=f"chat {ctx.endpoint_model_id}") as sp:
                sp.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "chat")
                sp.set_data(SPANDATA.GEN_AI_REQUEST_MODEL, ctx.endpoint_model_id)
                sp.set_data(SPANDATA.GEN_AI_PROVIDER_NAME, "openai")   # docs: required on chat; value for OpenAI-compatible endpoints is our choice
                sp.set_data(SPANDATA.GEN_AI_INPUT_MESSAGES, json.dumps(messages))
                resp = await client.chat.completions.create(...)
                sp.set_data(SPANDATA.GEN_AI_RESPONSE_MODEL, resp.model)
                sp.set_data(SPANDATA.GEN_AI_OUTPUT_MESSAGES, json.dumps([...]))
                sp.set_data(SPANDATA.GEN_AI_USAGE_INPUT_TOKENS, resp.usage.prompt_tokens)
                sp.set_data(SPANDATA.GEN_AI_USAGE_OUTPUT_TOKENS, resp.usage.completion_tokens)

            # ---- each tool call ----
            with sentry_sdk.start_span(op=OP.GEN_AI_EXECUTE_TOOL, name=f"execute_tool {tool_name}") as sp:
                sp.set_data(SPANDATA.GEN_AI_OPERATION_NAME, "execute_tool")
                sp.set_data(SPANDATA.GEN_AI_TOOL_NAME, tool_name)
                sp.set_data(SPANDATA.GEN_AI_TOOL_CALL_ARGUMENTS, json.dumps(args))
                result = await call_env_tool(...)
                sp.set_data(SPANDATA.GEN_AI_TOOL_CALL_RESULT, json.dumps(result))
                if result_is_error:
                    sp.set_status("internal_error")   # docs: mark failed tools with error status

            # ---- end of run (still INSIDE the transaction so the event links to this trace) ----
            scope.set_tag("passed", str(passed).lower())
            scope.set_tag("stop_reason", stop_reason)
            if not passed and ctx.purpose in ("traffic", "judge"):
                sentry_sdk.capture_message(
                    "agent_run_failed", level="warning",
                    fingerprint=["agent_run_failed", task.template_id, stop_reason],
                )
```

What the probe confirmed for this pattern with 3 concurrent runs under one FastAPI request:

- There was one transaction per run, named `invoke_agent ledger-agent`, with the gen_ai data on the transaction's trace context.
- Child `gen_ai.*` spans were sent as separate `span` envelope items (`version: 2`), with `parent_span_id` = the run transaction's span id and `sentry.segment.name` = `invoke_agent ledger-agent`. This is because `stream_gen_ai_spans` defaults to `True` (source: `sentry_sdk/tracing.py` `Transaction.finish`).
- Each run's tags appeared only on its own transaction and event.
- `gen_ai.conversation.id` = run_id appeared on the transaction and on every gen_ai child span. The SDK copies it from the scope at span finish for spans whose op starts with `gen_ai.` (source: `sentry_sdk/tracing.py` `Span.finish`).
- A transaction holds at most 1000 child spans by default (`_experiments={"max_spans": N}` raises it) (source: `sentry_sdk/scope.py:1224`). That is fine for our turn limits.

### 3.4 Conversation grouping (spec 8.8 "if supported")

**Supported.** `sentry_sdk.ai.set_conversation_id(run_id)` sets it on the current scope, and `Scope.remove_conversation_id()` unsets it (source: `sentry_sdk/ai/utils.py`, `sentry_sdk/scope.py`) (docs: https://docs.sentry.io/platforms/python/tracing/instrumentation/custom-instrumentation/ai-agents-module/). Call it inside the per-run `isolation_scope()`, because `isolation_scope()` forks the current scope too, so values don't leak between runs (probe). The docs also suggest `sentry_sdk.set_user(...)` for a User column in the Conversations view (optional for us).

## 4. Per-run isolation, tags, trace_id

- `with sentry_sdk.isolation_scope() as scope:` forks both the isolation scope and the current scope for the block. `sentry_sdk.new_scope()` forks only the current scope (source: `sentry_sdk/scope.py` `new_scope`, `isolation_scope`). Use `isolation_scope()` per run. `sentry_sdk.set_tag(...)` writes to the isolation scope (source: `sentry_sdk/api.py`).
- `asyncio.gather(*coros)` wraps each coroutine in a Task with a copied contextvars context. With `isolation_scope()` entered inside each coroutine, tags did not leak across 3 concurrent runs (probe).
- **trace_id:** use `root.trace_id` on the transaction, or `sentry_sdk.get_current_span().trace_id`, or `sentry_sdk.get_traceparent()` (`"<trace_id>-<span_id>-<sampled>"`) (source: `sentry_sdk/api.py`; probe).
- **Fresh trace per run:** inside a FastAPI request, `start_transaction(op=, name=)` with no `trace_id` creates a **new trace_id per run**, not the incoming request's (probe; `Span.trace_id` generates `uuid4().hex` when unset, source: `sentry_sdk/tracing.py`). To join the caller's trace instead, use `continue_trace` (section 9).
- **Pitfall:** anything captured inside `isolation_scope()` but **outside** the run transaction (events, logs) carries the *request's* trace_id, not the run's (probe). Capture `agent_run_failed` and run logs inside the `with start_transaction(...)` block.

## 5. Failed-run issues

```python
sentry_sdk.capture_message(
    "agent_run_failed", level="warning",
    fingerprint=["agent_run_failed", template_id, stop_reason],
)
# equivalent: scope.fingerprint = [...]; scope.capture_message("agent_run_failed", level="warning")
```

`capture_message(message, level=None, scope=None, **scope_kwargs)`, where the scope kwargs are `user, level, extras, contexts, tags, fingerprint, attributes` (source: `sentry_sdk/api.py`, `sentry_sdk/scope.py:update_from_kwargs`). In the probe, the event had `level: "warning"`, the given fingerprint, and the run's isolation-scope tags.

## 6. Logs

**Enablement (installed 2.72.0):**

- `sentry_sdk.logger.{trace,debug,info,warning,error,fatal}(template, **params, attributes={...})` sends Sentry Logs **regardless of `enable_logs`**. Probe: logs were sent with `enable_logs=False`. This matches the docs' "Logging is on by default" (source: `sentry_sdk/logger.py`; docs: https://docs.sentry.io/platforms/python/logs/). Minimum SDK for Logs: 2.35.0 (docs: same).
- **stdlib `logging` is NOT forwarded by default.** Forward it with `LoggingIntegration(capture_sentry_logs=True)`, the recommended way. The `enable_logs=True` init option (default `False`) is a deprecated fallback that applies only when `capture_sentry_logs` is unset. `_experiments={"enable_logs": True}` is also still honored (source: `sentry_sdk/integrations/logging.py` `SentryLogsHandler.emit`, `sentry_sdk/consts.py` `enable_logs: bool = False`; docs: https://docs.sentry.io/platforms/python/integrations/logging/). `sentry_logs_level` defaults to INFO (source). The SDK won't see records below the logger's own level, and uvicorn may reset logging config, so use `logging.basicConfig(level=..., force=True)` (docs: integrations/logging).

**Attributes:**

- `logger.info("msg %s", x, extra={"run_id": ..., "arm": ...})` makes each `extra` key a top-level, searchable log attribute (e.g. `run_id:abc`). Non-primitive extras are stringified (probe: `{'a': 1}` became the string `"{'a': 1}"`) (source: `SentryLogsHandler._extra_from_record`; docs: integrations/logging).
- Attributes set with `scope.set_attribute(s)` / `sentry_sdk.set_attribute` (isolation scope) / `get_global_scope().set_attribute` are added to **every log** captured in that scope (source: `sentry_sdk/scope.py` `apply_to_telemetry`; probe in static mode). The Python logs docs say `sentry_sdk.set_attribute` applies "only in stream mode". In 2.72.0 static mode it **does** apply to logs; per that statement it does not apply to spans.
- Logs automatically get `trace_id` (and `span_id` when a span is active) from the scope, so run logs link to the run trace when emitted inside the transaction (probe).
- Scope **tags are not** log attributes (probe). Set both tags and attributes (section 3.3).

**Our JSON-stdout convention (spec 7.4) plus Sentry.** Keep the JSON formatter on the stdout handler and pass structured fields via `extra=`. Sentry gets `record.getMessage()` as the body and the extras as attributes. Do **not** pre-serialize the JSON into the message string, or nothing is filterable.

**Copy-paste snippet for env / train services:**

```python
# scar_<svc>/observability.py
import json, logging, os, sys, time
import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

SERVICE = "env"  # or "train"

class JsonFormatter(logging.Formatter):
    KEYS = ("run_id", "task_id", "arm", "seed", "generation", "model_version")
    def format(self, r):
        d = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(r.created)), "level": r.levelname,
             "service": SERVICE, "msg": r.getMessage()}
        d.update({k: getattr(r, k) for k in self.KEYS if hasattr(r, k)})
        return json.dumps(d)

def setup() -> None:
    h = logging.StreamHandler(sys.stdout); h.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[h], force=True)
    sentry_sdk.init(
        dsn=os.environ.get("SENTRY_DSN"),
        environment=os.environ.get("SENTRY_ENVIRONMENT", "hackathon"),
        traces_sample_rate=1.0,
        send_default_pii=True,
        integrations=[LoggingIntegration(capture_sentry_logs=True, sentry_logs_level=logging.INFO)],
    )
    sentry_sdk.get_global_scope().set_tag("service", SERVICE)
    sentry_sdk.get_global_scope().set_attribute("service", SERVICE)

# usage: logging.getLogger(__name__).info("tool executed", extra={"run_id": rid, "task_id": tid})
```

## 7. OpenAI auto-instrumentation

- `OpenAIIntegration` is **auto-enabling** whenever `openai>=1.0` is importable. The same goes for `HttpxIntegration`, `FastApiIntegration`, `StarletteIntegration`, and `AnthropicIntegration` (source: `sentry_sdk/integrations/__init__.py` `_AUTO_ENABLING_INTEGRATIONS`, min versions).
- Probe with `openai` 3.34.0: one `chat.completions.create` inside a transaction produced an automatic span `op=gen_ai.chat`, `name="chat m1"`. It carried `gen_ai.request.model`, `gen_ai.request.messages` (the deprecated key), `gen_ai.response.text`, `gen_ai.system`, and the usage tokens. **Our manual `gen_ai.chat` spans would be duplicated.**
- Disable it with `disabled_integrations=[OpenAIIntegration()]`. Probe: the integration was absent from `client.integrations` and no auto gen_ai span appeared. `default_integrations=False` also disables all auto-enabling integrations, including FastAPI, logging and httpx, so **don't** use that (source: `sentry_sdk/consts.py` docstring).
- The `httpx` integration (the OpenAI client uses httpx) still adds an `http.client` span per request and attaches `sentry-trace`/`baggage` to **every** outgoing request, because `trace_propagation_targets` defaults to `[MATCH_ALL]` (source: `sentry_sdk/consts.py`; probe). Upside: agent→env HTTP calls carry the run's trace, so env's FastAPI spans join the run trace.
- Alternative (not chosen by spec): keep `OpenAIIntegration` and drop the manual chat span. It would emit deprecated keys, and its `include_prompts` option exists (source: `sentry_sdk/integrations/openai.py`).

## 8. Next.js setup (`@sentry/nextjs` 11.8.0, App Router)

Files (docs: https://docs.sentry.io/platforms/javascript/guides/nextjs/manual-setup/): `next.config.ts`, `instrumentation.ts`, `instrumentation-client.ts`, `sentry.server.config.ts`, `sentry.edge.config.ts`, `app/global-error.tsx`. Requirements: Next.js 14+ and Node 20.19+. `onRequestError` needs SDK 8.28+ and Next 15.

```ts
// instrumentation-client.ts   (browser; Replay lives ONLY here)
import * as Sentry from "@sentry/nextjs";

Sentry.init({
  dsn: process.env.NEXT_PUBLIC_SENTRY_DSN,
  environment: "hackathon",
  tracesSampleRate: 1.0,
  integrations: [Sentry.replayIntegration()],   // defaults: maskAllText, maskAllInputs, blockAllMedia = true
  replaysSessionSampleRate: 1.0,                // "every page"; mind the 50-replay free quota (section 11)
  replaysOnErrorSampleRate: 1.0,
  // Only needed if the browser calls the agent service DIRECTLY (cross-origin):
  tracePropagationTargets: ["localhost", /^\/api\//, /^http:\/\/localhost:8002/],
});

export const onRouterTransitionStart = Sentry.captureRouterTransitionStart;
```

```ts
// sentry.server.config.ts   (and sentry.edge.config.ts, same shape)
import * as Sentry from "@sentry/nextjs";
Sentry.init({ dsn: process.env.NEXT_PUBLIC_SENTRY_DSN, tracesSampleRate: 1.0 });
```

```ts
// instrumentation.ts
import * as Sentry from "@sentry/nextjs";
export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs") await import("./sentry.server.config");
  if (process.env.NEXT_RUNTIME === "edge") await import("./sentry.edge.config");
}
export const onRequestError = Sentry.captureRequestError;
```

```ts
// next.config.ts
import type { NextConfig } from "next";
import { withSentryConfig } from "@sentry/nextjs/config";   // v11: exported from "/config"
const nextConfig: NextConfig = {};
export default withSentryConfig(nextConfig, {
  org: process.env.SENTRY_ORG, project: process.env.SENTRY_PROJECT,
  silent: !process.env.CI,
  authToken: process.env.SENTRY_AUTH_TOKEN,   // optional, source maps
  // tunnelRoute: "/sentry-tunnel",           // optional, ad-blockers; exclude from middleware/proxy matcher
});
```

`app/global-error.tsx`: a client component that calls `Sentry.captureException(error)` in `useEffect` and renders `<NextError statusCode={0} />` (docs: manual-setup, code copied there).

Facts:

- `browserTracingIntegration()` is added **automatically** by `@sentry/nextjs`'s client `getDefaultIntegrations` (source: tarball `build/esm/client/index.js`, `getDefaultIntegrations`). No need to list it.
- `withSentryConfig` is exported from `@sentry/nextjs/config` in 11.8.0 (source: tarball `package.json` exports `./config`, `build/types/config/index.d.ts`). `captureRouterTransitionStart` and `captureRequestError` are exported from the root (source: tarball types).
- Replay: configure only in `instrumentation-client.ts`. Defaults mask all text and inputs and block media. Recommended CSP is `worker-src 'self' blob:`. Keep `replaysOnErrorSampleRate: 1.0` (docs: https://docs.sentry.io/platforms/javascript/guides/nextjs/session-replay/).

## 9. Distributed tracing web → agent service

- Browser: "all outgoing requests to the same origin will be propagated by default". Server (Node): "all outgoing requests will be propagated by default" (docs: https://docs.sentry.io/platforms/javascript/guides/nextjs/configuration/options/).
- **Next.js route handler proxying to FastAPI (recommended):** the browser calls `/api/...` on the same origin, so `sentry-trace` and `baggage` are attached automatically. The route handler continues that trace. Its server-side `fetch` to `http://agent:8002/...` gets `sentry-trace`, `baggage` (and `traceparent`) from `nativeNodeFetchIntegration`, which is on by default since v8 with `tracePropagation: true` (docs: https://docs.sentry.io/platforms/javascript/guides/nextjs/configuration/integrations/nodefetch/). No CORS is involved. Fallback if headers are missing: `const t = Sentry.getTraceData(); fetch(url, { headers: { "sentry-trace": t["sentry-trace"], baggage: t.baggage } })` (docs: https://docs.sentry.io/platforms/javascript/guides/nextjs/tracing/distributed-tracing/custom-instrumentation/). Not tested end to end here.
- **Browser calling FastAPI directly (cross-origin):** add the backend to `tracePropagationTargets` (port matters), and add FastAPI CORS `allow_headers=["sentry-trace", "baggage"]`, i.e. `Access-Control-Allow-Headers: sentry-trace, baggage` (docs: https://docs.sentry.io/platforms/javascript/guides/nextjs/tracing/distributed-tracing/dealing-with-cors-issues/).
- **FastAPI side:** the ASGI/FastAPI integration auto-continues incoming `sentry-trace`/`baggage`. In the probe, the request transaction had the incoming trace_id and parent span id (source: `sentry_sdk/integrations/asgi.py` calls `continue_trace(_get_headers(scope), ...)`).
- **Linking the run to the click:** because each run opens its own transaction, the run gets a **new trace** unless you continue explicitly (section 4). For `/ask` (judge, one run per click), capture `sentry_sdk.get_traceparent()` / `sentry_sdk.get_baggage()` in the endpoint, then in the run use `sentry_sdk.start_transaction(sentry_sdk.continue_trace({"sentry-trace": tp, "baggage": bg}, op=..., name=...))`. Probe: the run transaction then had the incoming trace_id with `parent_span_id` = the request transaction's span, and its gen_ai spans and failure event shared that trace. For `/agent/run_batch` (up to 64 runs), keep a fresh trace per run so each trace link shows one run.

## 10. Trace link URL

`https://{org_slug}.sentry.io/explore/traces/trace/{trace_id}/`, or the non-customer-domain form `https://sentry.io/organizations/{org_slug}/explore/traces/trace/{trace_id}/`.

- Source: Sentry's open-source frontend `getsentry/sentry` `static/app/router/routes.tsx` (master, fetched 2026-10-17). `exploreRoutes = {path: '/explore/', withOrgPath: true}` → child `traces/` → child `traceView = {path: 'trace/:traceSlug/'}`. `withOrgPath` renders both the bare path (customer domain `{org}.sentry.io`) and `/organizations/:orgId/...` (`static/app/router/types.tsx`).
- This is **not stated in docs.sentry.io**. The trace-view docs only give `https://sentry.io/orgredirect/organizations/:orgslug/explore/traces/` (docs: https://docs.sentry.io/concepts/key-terms/tracing/trace-view/).
- The project isn't part of the path, so `SENTRY_PROJECT` is not needed for the link. Whether `?project=<id>` or a timestamp query param improves loading is **UNKNOWN**. Click once in the UI and copy the address bar to confirm.

## 11. Quota notes

- **Developer (free) plan:** 1 user, 5k errors, **5M spans**, **5GB logs**, **50 session replays**, 1 uptime + 1 cron monitor, 1GB attachments, 30-day lookback. It includes "Agent tracing" and "MCP monitoring" (official pricing page https://sentry.io/pricing/, not docs.sentry.io). Whether these allowances are monthly is **UNKNOWN** from docs.
- Overage: "Events and attachments that exceed your quota will not be accepted" (docs: https://docs.sentry.io/pricing/quotas/). The free plan's ability to buy PAYG is **UNKNOWN**. Paid PAYG spans cost ~$0.000002 each (Team) and logs $0.50/GB (docs: https://docs.sentry.io/pricing/).
- Rough span count per run: 1 transaction + 1 span per model call + 1 per tool call, plus the `http.client` span per httpx request (OpenAI and env calls). Per docs, a span is the billing unit, and whether the transaction itself counts as a span is **UNKNOWN**. Example: 10 turns with 1 tool each is about 41 spans per run, so 5M spans covers about 120k runs. To cut volume, pass `disabled_integrations=[HttpxIntegration()]`. That loses automatic header propagation to env, so you would then pass `sentry-trace`/`baggage` manually. Alternatively, lower `traces_sample_rate` for `purpose="eval"`. The spec says AI traces should be sampled at 100%, so prefer sampling whole runs with `traces_sampler`, not dropping spans.
- **Replays:** 50 per plan period. `replaysSessionSampleRate: 1.0` will burn that in one afternoon of dev. Use 1.0 only for the demo and turn it down during development (e.g. 0 in dev, rely on `replaysOnErrorSampleRate`).

## 12. Open questions / contradictions with SPEC 8.8

1. **Tool I/O keys:** use `gen_ai.tool.call.arguments` and `gen_ai.tool.call.result`. `gen_ai.tool.input` and `gen_ai.tool.output` exist but are deprecated.
2. **Chat span required attrs:** the docs also mark `gen_ai.provider.name` and `gen_ai.response.model` as required on chat spans. The spec omits both, so add them.
3. **Message format:** the docs want `gen_ai.input.messages` in `{role, parts:[{type, content}]}` form. The spec only says "JSON string"; we hold OpenAI-format messages. A converter is needed, and the exact `parts` schema for tool calls is unverified.
4. **Root span API:** in static mode the run root must be `start_transaction`, not `start_span`. The docs' AI examples assume an existing transaction.
5. **Logs enablement:** the spec phrase "enable logs" maps to `LoggingIntegration(capture_sentry_logs=True)`. `enable_logs=True` is deprecated but works, and `sentry_sdk.logger` needs nothing.
6. **Tags vs log attributes:** scope tags don't reach logs, so set both (`set_tags` + `set_attributes`).
7. **Web → run trace linking** needs an explicit `continue_trace` in the run (section 9). Otherwise the click's trace stops at the HTTP request transaction.
8. **OpenAI auto-instrumentation duplicates chat spans.** Disable `OpenAIIntegration` in the agent service.
9. **Trace URL** is verified only from frontend source, not docs.
10. **Quotas:** 50 free replays and 5M spans can be exhausted by "Replay on every page" plus large batches.
11. Not verified here: the Sentry CLI/MCP conversation export commands (F13 remainder), the exact Next.js 16 `proxy.ts` matcher for `tunnelRoute`, and an end-to-end send to a real DSN.
