import json
from datetime import datetime, timezone
from decimal import Decimal

from scar_schemas import constants
from scar_schemas.ids import new_id
from scar_schemas.models import (
    ChatMessage,
    ExpectedAnswer,
    JournalEntrySpec,
    JournalLine,
    Task,
    ToolCall,
)
from scar_schemas.openai_format import dumps_args, from_openai_message, load_tools, to_openai_messages


def test_constants():
    assert constants.SCHEMA_VERSION == "1.0.0"
    assert constants.TOOLS_VERSION == "tools_v1"
    assert constants.SEEDS == (0, 1, 2)


def test_new_id_format():
    i = new_id("task")
    assert i.startswith("task_") and len(i) == len("task_") + 32


def test_task_json_round_trip_keeps_decimals():
    t = Task(
        task_id=new_id("task"),
        company_id="company-004",
        split="train",
        family="bookkeeping",
        template_id="book.accrual",
        params={"amount": "100.00"},
        prompt="Accrue 100.00",
        expected=ExpectedAnswer(
            answer_type="journal_entry",
            journal_entries=[
                JournalEntrySpec(
                    entry_date="2026-01-31",
                    lines=[
                        JournalLine(account_id="a", debit=Decimal("100.00")),
                        JournalLine(account_id="b", credit=Decimal("100.00")),
                    ],
                )
            ],
        ),
        difficulty=1,
        origin="seed",
        created_at=datetime.now(timezone.utc),
    )
    dumped = t.model_dump(mode="json")
    assert dumped["expected"]["journal_entries"][0]["lines"][0]["debit"] == "100.00"
    assert Task.model_validate(json.loads(json.dumps(dumped))) == t


def test_load_tools():
    tools = load_tools()
    names = [t["function"]["name"] for t in tools]
    assert names == [
        "get_accounts",
        "get_vendors",
        "get_transactions",
        "sql_query",
        "create_journal_entry",
        "submit_answer",
    ]


def test_dumps_args_is_deterministic():
    assert dumps_args({"b": 1, "a": "é"}) == '{"a":"é","b":1}'


def test_openai_round_trip():
    msgs = [
        ChatMessage(role="system", content="sys"),
        ChatMessage(role="user", content="q"),
        ChatMessage(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(id="c1", name="get_transactions", arguments={"limit": 5, "account_id": "x"}),
                ToolCall(id="c2", name="sql_query", arguments={"__raw__": "{not json"}),
            ],
        ),
        ChatMessage(role="tool", content='{"ok":true}', tool_call_id="c1", name="get_transactions"),
    ]
    oa = to_openai_messages(msgs)
    assert "content" not in oa[2]
    assert oa[2]["tool_calls"][0]["function"]["arguments"] == '{"account_id":"x","limit":5}'
    assert oa[2]["tool_calls"][1]["function"]["arguments"] == "{not json"
    assert oa[3] == {"role": "tool", "content": '{"ok":true}', "tool_call_id": "c1", "name": "get_transactions"}
    back = [from_openai_message(m) for m in oa]
    assert back == msgs
    assert to_openai_messages(back) == oa


def test_from_openai_message_bad_json_and_non_object():
    m = from_openai_message(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "a", "type": "function", "function": {"name": "f", "arguments": "nope"}},
                {"id": "b", "type": "function", "function": {"name": "f", "arguments": "[1]"}},
            ],
        }
    )
    assert m.tool_calls[0].arguments == {"__raw__": "nope"}
    assert m.tool_calls[1].arguments == {"__raw__": "[1]"}
