from datetime import date, datetime, timezone
from decimal import Decimal
from typing import get_args

import pytest
from psycopg.types.json import Jsonb
from scar_schemas.ids import new_id
from scar_schemas.models import ExpectedAnswer, JournalEntrySpec, JournalLine, StopReason, Task

from scar_env.store import store_task

RUN = "run_verify_test"
C1, C2 = "company-901", "company-902"
FAMILY = {"number": "aggregation", "id_set": "anomaly", "journal_entry": "bookkeeping"}


def make_task(conn, expected: ExpectedAnswer, company_id: str = C1) -> Task:
    task = Task(
        task_id=new_id("task"),
        company_id=company_id,
        split="train",
        family=FAMILY[expected.answer_type.value],
        template_id="test.template",
        params={},
        prompt="Test question.",
        expected=expected,
        difficulty=1,
        origin="seed",
        created_at=datetime.now(timezone.utc),
    )
    store_task(conn, task)
    return task


def verify(client, task_id: str, final_answer, stop_reason: str = "submitted", run_id: str = RUN) -> dict:
    body = {"task_id": task_id, "run_id": run_id, "stop_reason": stop_reason, "final_answer": final_answer}
    r = client.post("/verify", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def number_task(conn, number: str, tolerance: str = "0.01") -> Task:
    return make_task(conn, ExpectedAnswer(answer_type="number", number=Decimal(number), tolerance=Decimal(tolerance)))


def test_unknown_task_is_404(client, ledger):
    r = client.post(
        "/verify", json={"task_id": "task_nope", "run_id": RUN, "stop_reason": "submitted", "final_answer": {}}
    )
    assert r.status_code == 404
    assert r.json() == {"error": "not_found", "detail": "unknown task: task_nope"}


def test_database_failure_is_503(client, ledger, conn):
    task = number_task(conn, "1.00")
    client.app.state.db.pool.close()
    body = {"task_id": task.task_id, "run_id": RUN, "stop_reason": "submitted", "final_answer": {"number": 1}}
    r = client.post("/verify", json=body)
    assert r.status_code == 503 and r.json()["error"] == "db_unavailable"


@pytest.mark.parametrize("stop_reason", [r for r in get_args(StopReason) if r != "submitted"])
def test_not_submitted_fails_for_every_stop_reason(client, ledger, conn, stop_reason):
    task = number_task(conn, "1234.50")
    for final_answer in ({"number": 1234.5}, None):
        assert verify(client, task.task_id, final_answer, stop_reason) == {
            "passed": False,
            "detail": f"not submitted: {stop_reason}",
        }


# ---------- number ----------


@pytest.mark.parametrize(
    ("submitted", "passed", "got"),
    [
        (1234.5, True, "1234.5"),
        ("1234.50", True, "1234.50"),
        (1234.51, True, "1234.51"),  # exactly at the tolerance
        (1234.49, True, "1234.49"),
        (1234.511, False, "1234.511"),  # just over
        (1234.489, False, "1234.489"),
        (1243.5, False, "1243.5"),
        ("-1234.50", False, "-1234.50"),
    ],
)
def test_number_tolerance(client, ledger, conn, submitted, passed, got):
    task = number_task(conn, "1234.50")
    assert verify(client, task.task_id, {"number": submitted}) == {
        "passed": passed,
        "detail": f"number: expected 1234.50, got {got}",
    }


def test_number_custom_and_zero_tolerance(client, ledger, conn):
    share = number_task(conn, "12.34", tolerance="0.05")
    assert verify(client, share.task_id, {"number": 12.39})["passed"] is True
    assert verify(client, share.task_id, {"number": 12.40})["passed"] is False
    count = number_task(conn, "17", tolerance="0")
    assert verify(client, count.task_id, {"number": 17})["passed"] is True
    assert verify(client, count.task_id, {"number": 17.0})["passed"] is True
    assert verify(client, count.task_id, {"number": 17.001})["passed"] is False
    assert verify(client, count.task_id, {"number": 16})["passed"] is False


@pytest.mark.parametrize(
    ("final_answer", "detail"),
    [
        (None, "number: missing from final_answer"),
        ({}, "number: missing from final_answer"),
        ({"number": None}, "number: missing from final_answer"),
        ({"ids": ["x"]}, "number: missing from final_answer"),
        ({"number": True}, "number: not a number: True"),
        ({"number": False}, "number: not a number: False"),
        ({"number": "abc"}, "number: not a number: 'abc'"),
        ({"number": "1,234.50"}, "number: not a number: '1,234.50'"),
        ({"number": "NaN"}, "number: not a number: 'NaN'"),
        ({"number": "Infinity"}, "number: not a number: 'Infinity'"),
        ({"number": [1234.5]}, "number: not a number: [1234.5]"),
        ({"number": {"value": 1234.5}}, "number: not a number: {'value': 1234.5}"),
    ],
)
def test_number_missing_or_wrong_type(client, ledger, conn, final_answer, detail):
    task = number_task(conn, "1234.50")
    assert verify(client, task.task_id, final_answer) == {"passed": False, "detail": detail}


# ---------- id_set ----------


def id_task(conn, ids: list[str]) -> Task:
    return make_task(conn, ExpectedAnswer(answer_type="id_set", id_set=ids))


@pytest.mark.parametrize(
    ("ids", "passed", "detail"),
    [
        (["t1", "t2", "t3"], True, "ids: expected 3, got 3 distinct; missing []; extra []"),
        (["t3", "t1", "t2"], True, "ids: expected 3, got 3 distinct; missing []; extra []"),
        (["t2", "t1", "t3", "t1", "t2"], True, "ids: expected 3, got 3 distinct; missing []; extra []"),
        (["t1", "t3"], False, "ids: expected 3, got 2 distinct; missing ['t2']; extra []"),
        (["t1", "t2", "t3", "t9"], False, "ids: expected 3, got 4 distinct; missing []; extra ['t9']"),
        (["t9", "t1", "t8"], False, "ids: expected 3, got 3 distinct; missing ['t2', 't3']; extra ['t8', 't9']"),
        ([], False, "ids: expected 3, got 0 distinct; missing ['t1', 't2', 't3']; extra []"),
    ],
)
def test_id_set(client, ledger, conn, ids, passed, detail):
    task = id_task(conn, ["t1", "t2", "t3"])
    assert verify(client, task.task_id, {"ids": ids}) == {"passed": passed, "detail": detail}


@pytest.mark.parametrize(
    ("final_answer", "detail"),
    [
        ({}, "ids: missing from final_answer"),
        ({"number": 3}, "ids: missing from final_answer"),
        ({"ids": "t1,t2,t3"}, "ids: expected a list of strings, got 't1,t2,t3'"),
        ({"ids": [1, 2, 3]}, "ids: expected a list of strings, got [1, 2, 3]"),
        ({"ids": ["t1", None]}, "ids: expected a list of strings, got ['t1', None]"),
    ],
)
def test_id_set_missing_or_wrong_type(client, ledger, conn, final_answer, detail):
    task = id_task(conn, ["t1", "t2", "t3"])
    assert verify(client, task.task_id, final_answer) == {"passed": False, "detail": detail}


# ---------- journal_entry ----------


def acct(name: str, company: str = C1) -> str:
    return f"{company}:{name}"


def spec(entry_date: str, *lines: tuple[str, str, str]) -> JournalEntrySpec:
    return JournalEntrySpec(
        entry_date=entry_date,
        lines=[JournalLine(account_id=acct(a), debit=Decimal(d), credit=Decimal(c)) for a, d, c in lines],
    )


def journal_task(conn, *entries: JournalEntrySpec, company_id: str = C1) -> Task:
    return make_task(conn, ExpectedAnswer(answer_type="journal_entry", journal_entries=list(entries)), company_id)


def record(client, entry_date: str, *lines: tuple[str, float, float], company=C1, run_id=RUN, memo=None) -> str:
    """Write an entry through the real tool, as the agent would."""
    args = {
        "entry_date": entry_date,
        "lines": [
            {"account_id": acct(a, company), **({"debit": d} if d else {"credit": c}), "memo": memo}
            for a, d, c in lines
        ],
    }
    body = {"run_id": run_id, "company_id": company, "name": "create_journal_entry", "arguments": args}
    r = client.post("/tools/execute", json=body).json()
    assert r["ok"] is True, r
    return r["result"]["entry_id"]


ACCRUAL = spec("2026-03-31", ("acct-utilities", "312.40", "0"), ("acct-accounts-payable", "0", "312.40"))


def test_journal_entry_exact_match(client, ledger, conn):
    task = journal_task(conn, ACCRUAL)
    entry = record(client, "2026-03-31", ("acct-accounts-payable", 0, 312.4), ("acct-utilities", 312.4, 0), memo="x")
    assert verify(client, task.task_id, {"entry_ids": [entry]}) == {
        "passed": True,
        "detail": "journal_entry: matched 1 of 1 expected entries",
    }
    assert verify(client, task.task_id, {"entry_ids": [entry, entry]})["passed"] is True


def test_journal_entry_amounts_within_one_cent(client, ledger, conn):
    task = journal_task(conn, ACCRUAL)
    near = record(client, "2026-03-31", ("acct-utilities", 312.41, 0), ("acct-accounts-payable", 0, 312.41))
    far = record(client, "2026-03-31", ("acct-utilities", 312.42, 0), ("acct-accounts-payable", 0, 312.42))
    assert verify(client, task.task_id, {"entry_ids": [near]})["passed"] is True
    result = verify(client, task.task_id, {"entry_ids": [far]})
    assert result == {
        "passed": False,
        "detail": "journal_entry: matched 0 of 1 expected entries; unmatched submitted: ["
        f"'{far} 2026-03-31 (company-901:acct-accounts-payable, 0.00, 312.42),"
        " (company-901:acct-utilities, 312.42, 0.00)']; unmatched expected: ["
        "'2026-03-31 (company-901:acct-accounts-payable, 0, 312.40), (company-901:acct-utilities, 312.40, 0)']",
    }


@pytest.mark.parametrize(
    "lines_date",
    [
        ("2026-03-30", (("acct-utilities", 312.4, 0), ("acct-accounts-payable", 0, 312.4))),
        ("2026-03-31", (("acct-rent", 312.4, 0), ("acct-accounts-payable", 0, 312.4))),
        ("2026-03-31", (("acct-accounts-payable", 312.4, 0), ("acct-utilities", 0, 312.4))),
        ("2026-03-31", (("acct-utilities", 312.4, 0), ("acct-accounts-payable", 0, 300), ("acct-checking", 0, 12.4))),
    ],
)
def test_journal_entry_wrong_date_account_side_or_lines_fails(client, ledger, conn, lines_date):
    task = journal_task(conn, ACCRUAL)
    entry = record(client, lines_date[0], *lines_date[1])
    result = verify(client, task.task_id, {"entry_ids": [entry]})
    assert result["passed"] is False
    assert result["detail"].startswith(
        f"journal_entry: matched 0 of 1 expected entries; unmatched submitted: ['{entry} "
    )


def amortization(conn) -> Task:
    months = ("2026-01-31", "2026-02-28", "2026-03-31")
    return journal_task(
        conn,
        *[spec(m, ("acct-insurance", "400.00", "0"), ("acct-prepaid-expenses", "0", "400.00")) for m in months],
    )


def amortize(client, month: str, run_id=RUN) -> str:
    return record(client, month, ("acct-insurance", 400, 0), ("acct-prepaid-expenses", 0, 400), run_id=run_id)


def test_journal_entry_multiset_in_any_order(client, ledger, conn):
    task = amortization(conn)
    entries = [amortize(client, m) for m in ("2026-03-31", "2026-01-31", "2026-02-28")]
    assert verify(client, task.task_id, {"entry_ids": entries}) == {
        "passed": True,
        "detail": "journal_entry: matched 3 of 3 expected entries",
    }
    assert verify(client, task.task_id, {"entry_ids": entries[::-1]})["passed"] is True


def test_journal_entry_missing_extra_and_repeated_entries_fail(client, ledger, conn):
    task = amortization(conn)
    jan, feb, mar = (amortize(client, m) for m in ("2026-01-31", "2026-02-28", "2026-03-31"))
    missing = verify(client, task.task_id, {"entry_ids": [jan, feb]})
    assert missing == {
        "passed": False,
        "detail": "journal_entry: matched 2 of 3 expected entries; unmatched submitted: []; unmatched expected: "
        "['2026-03-31 (company-901:acct-insurance, 400.00, 0), (company-901:acct-prepaid-expenses, 0, 400.00)']",
    }
    second_mar = amortize(client, "2026-03-31")
    extra = verify(client, task.task_id, {"entry_ids": [jan, feb, mar, second_mar]})
    assert extra["passed"] is False and extra["detail"].startswith("journal_entry: matched 3 of 3 expected entries")
    assert "unmatched expected: []" in extra["detail"]
    assert verify(client, task.task_id, {"entry_ids": [jan, feb, feb]})["passed"] is False
    assert verify(client, task.task_id, {"entry_ids": []})["detail"].startswith("journal_entry: matched 0 of 3")


def test_journal_entry_identical_expected_entries_need_two_submitted(client, ledger, conn):
    task = journal_task(conn, ACCRUAL, ACCRUAL)
    first = record(client, "2026-03-31", ("acct-utilities", 312.4, 0), ("acct-accounts-payable", 0, 312.4))
    second = record(client, "2026-03-31", ("acct-utilities", 312.4, 0), ("acct-accounts-payable", 0, 312.4))
    assert verify(client, task.task_id, {"entry_ids": [first, first]})["passed"] is False
    assert verify(client, task.task_id, {"entry_ids": [second, first]})["passed"] is True


def insert_entry(conn, entry_id: str, entry: JournalEntrySpec, run_id: str = RUN, company_id: str = C1) -> None:
    conn.execute(
        "INSERT INTO app.sandbox_entries (entry_id, run_id, company_id, entry_date, lines, created_at)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (
            entry_id,
            run_id,
            company_id,
            date.fromisoformat(entry.entry_date),
            Jsonb([line.model_dump(mode="json") for line in entry.lines]),
            datetime.now(timezone.utc),
        ),
    )


def test_journal_entry_ties_within_tolerance_are_matched_one_to_one(client, ledger, conn):
    low = spec("2026-03-31", ("acct-utilities", "100.00", "0"), ("acct-accounts-payable", "0", "100.00"))
    high = spec("2026-03-31", ("acct-utilities", "100.02", "0"), ("acct-accounts-payable", "0", "100.02"))
    middle = spec("2026-03-31", ("acct-utilities", "100.01", "0"), ("acct-accounts-payable", "0", "100.01"))
    task = journal_task(conn, low, high)
    # entry_a fits both expected entries and is placed first; entry_b fits only `low`, so a greedy match would fail.
    insert_entry(conn, "entry_a", middle)
    insert_entry(conn, "entry_b", low)
    assert verify(client, task.task_id, {"entry_ids": ["entry_a", "entry_b"]}) == {
        "passed": True,
        "detail": "journal_entry: matched 2 of 2 expected entries",
    }


def test_journal_entry_ids_must_belong_to_this_run_and_company(client, ledger, conn):
    task = journal_task(conn, ACCRUAL)
    lines = (("acct-utilities", 312.4, 0), ("acct-accounts-payable", 0, 312.4))
    mine = record(client, "2026-03-31", *lines)
    other_run = record(client, "2026-03-31", *lines, run_id="run_other")
    other_company = record(client, "2026-03-31", *lines, company=C2)
    cases = [
        (["entry_nope"], "entry_ids: unknown ['entry_nope']"),
        ([other_run], f"entry_ids: from another run ['{other_run}']"),
        ([other_company], f"entry_ids: from another company ['{other_company}']"),
        ([mine, "entry_nope"], "entry_ids: unknown ['entry_nope']"),
    ]
    for entry_ids, detail in cases:
        assert verify(client, task.task_id, {"entry_ids": entry_ids}) == {"passed": False, "detail": detail}
    both = verify(client, task.task_id, {"entry_ids": [other_run, "entry_nope", other_company]})
    assert both["detail"] == (
        f"entry_ids: unknown ['entry_nope']; from another run ['{other_run}']; from another company ['{other_company}']"
    )


def test_journal_entry_unbalanced_stored_entry_fails(client, ledger, conn):
    task = journal_task(conn, ACCRUAL)
    insert_entry(
        conn,
        "entry_unbalanced",
        spec("2026-03-31", ("acct-utilities", "312.40", "0.00"), ("acct-accounts-payable", "0.00", "312.39")),
    )
    assert verify(client, task.task_id, {"entry_ids": ["entry_unbalanced"]}) == {
        "passed": False,
        "detail": "entry entry_unbalanced does not balance: debits 312.40, credits 312.39",
    }


@pytest.mark.parametrize(
    ("final_answer", "detail"),
    [
        (None, "entry_ids: missing from final_answer"),
        ({"ids": ["entry_x"]}, "entry_ids: missing from final_answer"),
        ({"entry_ids": "entry_x"}, "entry_ids: expected a list of strings, got 'entry_x'"),
        ({"entry_ids": [7]}, "entry_ids: expected a list of strings, got [7]"),
    ],
)
def test_journal_entry_missing_or_wrong_type(client, ledger, conn, final_answer, detail):
    task = journal_task(conn, ACCRUAL)
    assert verify(client, task.task_id, final_answer) == {"passed": False, "detail": detail}
