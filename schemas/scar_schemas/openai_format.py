# scar_schemas/openai_format.py — SHARED CONTRACT (signatures and behavior)
import json
from importlib import resources

from .models import ChatMessage, ToolCall


def dumps_args(args: dict) -> str:
    """Deterministic JSON for tool arguments: sort_keys=True, separators=(",", ":"), ensure_ascii=False."""
    return json.dumps(args, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def to_openai_messages(messages: list[ChatMessage]) -> list[dict]:
    """Convert to OpenAI chat format.
    assistant tool calls -> {"id": c.id, "type": "function",
                             "function": {"name": c.name, "arguments": dumps_args(c.arguments)}}
    tool messages        -> {"role": "tool", "tool_call_id": ..., "name": ..., "content": ...}
    Omit keys whose value is None. If arguments == {"__raw__": s}, emit s unchanged as the arguments string."""
    out = []
    for m in messages:
        d: dict = {"role": m.role}
        if m.content is not None:
            d["content"] = m.content
        if m.tool_calls is not None:
            d["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": _args_string(c.arguments)},
                }
                for c in m.tool_calls
            ]
        if m.tool_call_id is not None:
            d["tool_call_id"] = m.tool_call_id
        if m.name is not None:
            d["name"] = m.name
        out.append(d)
    return out


def _args_string(arguments: dict) -> str:
    if set(arguments) == {"__raw__"} and isinstance(arguments["__raw__"], str):
        return arguments["__raw__"]
    return dumps_args(arguments)


def from_openai_message(msg: dict) -> ChatMessage:
    """Inverse of the above for one message returned by the model.
    Parse function.arguments with json.loads; on failure store {"__raw__": original_string}."""
    tool_calls = None
    if msg.get("tool_calls"):
        tool_calls = []
        for c in msg["tool_calls"]:
            raw = c["function"].get("arguments") or ""
            try:
                args = json.loads(raw)
                if not isinstance(args, dict):
                    args = {"__raw__": raw}
            except (json.JSONDecodeError, TypeError):
                args = {"__raw__": raw}
            tool_calls.append(ToolCall(id=c["id"], name=c["function"]["name"], arguments=args))
    return ChatMessage(
        role=msg["role"],
        content=msg.get("content"),
        tool_calls=tool_calls,
        tool_call_id=msg.get("tool_call_id"),
        name=msg.get("name"),
    )


def load_tools(version: str = "tools_v1") -> list[dict]:
    """Return the tool list from scar_schemas/tools_v1.json (OpenAI `tools` format)."""
    return json.loads(resources.files("scar_schemas").joinpath(f"{version}.json").read_text())
