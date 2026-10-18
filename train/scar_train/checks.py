"""Tool-call check (SPEC 9.8): before a model version is registered, one chat completion with the tools must return
a tool call that parses. Called by register_base (P3-04) and by the loop before registering a candidate."""

import httpx
import openai
from openai import OpenAI
from scar_schemas.constants import MAX_TOKENS_PER_TURN, TOOLS_VERSION
from scar_schemas.openai_format import from_openai_message, load_tools

MODEL_TIMEOUT_S = 60  # SPEC 7.4: model call per turn
SYSTEM_PROMPT = "You are a bookkeeping assistant. Answer by calling the tools."
USER_MESSAGE = "Company: Test\n\nList the accounts."


class ToolCallCheckError(RuntimeError):
    """The endpoint failed, or answered without a parseable tool call."""


def tool_call_check(
    endpoint_base_url: str, endpoint_model_id: str, api_key: str | None, *, http_client: httpx.Client | None = None
) -> None:
    """Raise ToolCallCheckError unless the endpoint returns at least one tool call and every tool call's arguments
    parse as JSON (no `__raw__`), using the agent's own conversion (from_openai_message)."""
    where = f"{endpoint_base_url} model {endpoint_model_id}"
    client = OpenAI(
        base_url=endpoint_base_url,
        api_key=api_key or "none",
        timeout=MODEL_TIMEOUT_S,
        max_retries=2,
        http_client=http_client,
    )
    try:
        resp = client.chat.completions.create(
            model=endpoint_model_id,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": USER_MESSAGE}],
            tools=load_tools(TOOLS_VERSION),
            tool_choice="auto",
            temperature=0,
            max_tokens=MAX_TOKENS_PER_TURN,
        )
        msg = from_openai_message(resp.choices[0].message.model_dump())
    except (openai.APIError, IndexError, KeyError, ValueError) as e:
        raise ToolCallCheckError(f"tool-call check failed at {where}: {e!r}") from e
    if not msg.tool_calls:
        raise ToolCallCheckError(f"tool-call check failed at {where}: no tool call returned (content {msg.content!r})")
    raw = [c.name for c in msg.tool_calls if "__raw__" in c.arguments]
    if raw:
        raise ToolCallCheckError(f"tool-call check failed at {where}: unparseable arguments for tool call(s) {raw}")
