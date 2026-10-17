import re
import time
from datetime import date, datetime, timezone
from decimal import Decimal

import psycopg
import pytest
from scar_schemas.models import JournalLine, ToolExecuteRequest

from scar_env.tools import execute, harden_sql_tool

RUN = "run_tools_test"
C1, C2, EVAL = "company-901", "company-902", "company-903"
TXN_COLUMNS = ["txn_id", "txn_date", "amount", "account_id", "vendor_id", "description", "txn_type"]


@pytest.fixture(autouse=True)
def _hardened(conn):
    harden_sql_tool(conn)


def call(client, name, args=None, company=C1, run_id=RUN) -> dict:
    body = {"run_id": run_id, "company_id": company, "name": name, "arguments": args or {}}
    r = client.post("/tools/execute", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def ok(client, name, args=None, company=C1, run_id=RUN):
    body = call(client, name, args, company, run_id)
    assert body["ok"] is True and body["error"] is None, body
    return body["result"]


def err(client, name, args=None, company=C1, run_id=RUN) -> str:
    body = call(client, name, args, company, run_id)
    assert body["ok"] is False and body["result"] is None, body
    return body["error"]


def table(columns, rows, has_more=False) -> dict:
    return {"columns": columns, "has_more": has_more, "row_count": len(rows), "rows": [list(r) for r in rows]}


def txn_row(t) -> list:
    return [t.txn_id, t.txn_date.isoformat(), str(t.amount), t.account_id, t.vendor_id, t.description, t.txn_type]


def expected_txns(ledger, pred=lambda t: True, company=C1) -> list[list]:
    txns = sorted((t for t in ledger.company_txns(company) if pred(t)), key=lambda t: (t.txn_date, t.txn_id))
    return [txn_row(t) for t in txns]


def sandbox_count(conn) -> int:
    return conn.execute("SELECT count(*) FROM app.sandbox_entries").fetchone()[0]


# ---------- request handling ----------


def test_unknown_company_is_404(client, ledger):
    r = client.post(
        "/tools/execute", json={"run_id": RUN, "company_id": "company-999", "name": "get_accounts", "arguments": {}}
    )
    assert r.status_code == 404
    assert r.json() == {"error": "not_found", "detail": "unknown company: company-999"}


def test_unknown_tool_and_submit_answer_are_bad_input(client, ledger):
    assert "unknown tool 'drop_tables'" in err(client, "drop_tables")
    assert "handled by the agent" in err(client, "submit_answer", {"number": 1})


def test_unknown_and_missing_arguments(client, ledger):
    assert err(client, "get_accounts", {"company_id": C2}) == "unknown argument(s): company_id; allowed: none"
    assert "unknown argument(s): company_id" in err(client, "get_transactions", {"company_id": C2})
    assert err(client, "sql_query") == "missing required argument(s): sql"
    assert err(client, "create_journal_entry", {"lines": []}) == "missing required argument(s): entry_date"


def test_database_failure_is_503(client, ledger):
    client.app.state.db.pool.close()
    r = client.post("/tools/execute", json={"run_id": RUN, "company_id": C1, "name": "get_accounts", "arguments": {}})
    assert r.status_code == 503 and r.json()["error"] == "db_unavailable"


def test_tool_timeout_is_503(client, ledger, db_url):
    with psycopg.connect(db_url) as locker:
        locker.execute("LOCK TABLE ledger.accounts IN ACCESS EXCLUSIVE MODE")
        start = time.monotonic()
        r = client.post(
            "/tools/execute", json={"run_id": RUN, "company_id": C1, "name": "get_accounts", "arguments": {}}
        )
        elapsed = time.monotonic() - start
        locker.rollback()
    assert r.status_code == 503
    assert r.json() == {"error": "tool_timeout", "detail": "get_accounts exceeded the 10 s tool timeout"}
    assert 9.5 < elapsed < 15


def test_results_are_deterministic(client, ledger):
    for name, args in [
        ("get_accounts", {}),
        ("get_transactions", {"limit": 500}),
        ("sql_query", {"sql": "SELECT txn_date, sum(amount) FROM ledger.transactions GROUP BY 1 ORDER BY 1"}),
    ]:
        body = {"run_id": RUN, "company_id": C1, "name": name, "arguments": args}
        first = client.post("/tools/execute", json=body).content
        assert client.post("/tools/execute", json=body).content == first


# ---------- get_accounts, get_vendors ----------


def test_get_accounts(client, ledger):
    for company in (C1, EVAL):
        rows = sorted((a, name, typ) for a, cid, name, typ in ledger.accounts if cid == company)
        assert ok(client, "get_accounts", company=company) == table(["account_id", "name", "account_type"], rows)


def test_get_vendors(client, ledger):
    columns = ["vendor_id", "name"]
    rows = sorted((v, name) for v, cid, name in ledger.vendors if cid == C1)
    assert ok(client, "get_vendors") == table(columns, rows)
    assert ok(client, "get_vendors", {"name_contains": None}) == table(columns, rows)
    assert ok(client, "get_vendors", {"name_contains": "sWIFT haul"}) == table(
        columns, [("company-901:vend-freight-vendor", "Swift Haul Logistics")]
    )
    assert ok(client, "get_vendors", {"name_contains": "%"}) == table(columns, [])
    assert ok(client, "get_vendors", {"name_contains": "_"}) == table(columns, [])
    assert err(client, "get_vendors", {"name_contains": 5}) == "name_contains must be a string, got 5"


# ---------- get_transactions ----------


def test_get_transactions_default_returns_company_rows_in_order(client, ledger):
    rows = expected_txns(ledger)
    assert 60 < len(rows) < 100
    assert ok(client, "get_transactions") == {
        "columns": TXN_COLUMNS,
        "has_more": False,
        "row_count": len(rows),
        "rows": rows,
    }
    assert any(r[2] == "-262.50" for r in rows) and any(r[4] is None for r in rows)


@pytest.mark.parametrize(
    ("args", "pred"),
    [
        (
            {"start_date": "2026-02-01", "end_date": "2026-02-15"},
            lambda t: date(2026, 2, 1) <= t.txn_date <= date(2026, 2, 15),
        ),
        ({"account_id": "company-901:acct-freight"}, lambda t: t.account_id == "company-901:acct-freight"),
        ({"vendor_id": "company-901:vend-landlord"}, lambda t: t.vendor_id == "company-901:vend-landlord"),
        ({"min_amount": 1200, "max_amount": 1200.0}, lambda t: t.amount == Decimal("1200.00")),
        ({"min_amount": 2950.37}, lambda t: t.amount >= Decimal("2950.37")),
        ({"max_amount": 0}, lambda t: t.amount <= 0),
        ({"account_id": "company-902:acct-rent"}, lambda t: False),
        ({"start_date": None, "vendor_id": None, "limit": None}, lambda t: True),
    ],
)
def test_get_transactions_filters(client, ledger, args, pred):
    rows = expected_txns(ledger, pred)
    assert ok(client, "get_transactions", {"limit": 500, **args})["rows"] == rows


def test_get_transactions_filters_hit_inclusive_edges(client, ledger):
    rows = ok(client, "get_transactions", {"start_date": "2026-02-01", "end_date": "2026-02-15"})["rows"]
    assert rows[0][1] == "2026-02-01" and rows[-1][1] == "2026-02-15"
    rows = ok(client, "get_transactions", {"min_amount": 1200, "max_amount": 1200})["rows"]
    assert [r[2] for r in rows] == ["1200.00", "1200.00"]


def test_get_transactions_limit_and_has_more(client, ledger):
    rows = expected_txns(ledger)
    assert ok(client, "get_transactions", {"limit": 10}) == {
        "columns": TXN_COLUMNS,
        "has_more": True,
        "row_count": 10,
        "rows": rows[:10],
    }
    assert ok(client, "get_transactions", {"limit": len(rows)})["has_more"] is False
    assert ok(client, "get_transactions", {"limit": len(rows) - 1})["has_more"] is True
    assert ok(client, "get_transactions", {"limit": 1})["rows"] == rows[:1]


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"start_date": "2026/01/01"}, "start_date must be a date formatted YYYY-MM-DD, got '2026/01/01'"),
        ({"end_date": "2026-02-30"}, "end_date must be a date formatted YYYY-MM-DD, got '2026-02-30'"),
        ({"start_date": "20260101"}, "start_date must be a date formatted YYYY-MM-DD, got '20260101'"),
        ({"start_date": 20260101}, "start_date must be a date formatted YYYY-MM-DD, got 20260101"),
        ({"limit": 0}, "limit must be an integer from 1 to 500, got 0"),
        ({"limit": 501}, "limit must be an integer from 1 to 500, got 501"),
        ({"limit": True}, "limit must be an integer from 1 to 500, got True"),
        ({"limit": "10"}, "limit must be an integer from 1 to 500, got '10'"),
        ({"limit": 10.5}, "limit must be an integer from 1 to 500, got 10.5"),
        ({"min_amount": "100"}, "min_amount must be a number, got '100'"),
        ({"max_amount": False}, "max_amount must be a number, got False"),
        ({"account_id": 123}, "account_id must be a string, got 123"),
        ({"vendor_id": ["a"]}, "vendor_id must be a string, got ['a']"),
    ],
)
def test_get_transactions_bad_arguments(client, ledger, args, message):
    assert err(client, "get_transactions", args) == message


# ---------- sql_query ----------


def sql(client, query, company=C1):
    return ok(client, "sql_query", {"sql": query}, company=company)


def sql_err(client, query) -> str:
    return err(client, "sql_query", {"sql": query})


def test_sql_query_sees_only_the_current_company(client, ledger):
    for company in (C1, EVAL):
        counts = sql(client, "SELECT company_id, count(*) FROM ledger.transactions GROUP BY 1 ORDER BY 1", company)
        assert counts["rows"] == [[company, len(ledger.company_txns(company))]]
        for tbl in ("ledger.accounts", "ledger.vendors"):
            assert sql(client, f"SELECT DISTINCT company_id FROM {tbl}", company)["rows"] == [[company]]
    assert sql(client, "SELECT * FROM ledger.transactions WHERE company_id = 'company-903'")["rows"] == []
    assert sql(client, "SELECT * FROM ledger.accounts WHERE account_id = 'company-902:acct-rent'")["rows"] == []


def test_sql_query_result_format(client, ledger):
    res = sql(client, "SELECT txn_id, txn_date, amount FROM ledger.transactions ORDER BY txn_date, txn_id LIMIT 2")
    assert res == table(["txn_id", "txn_date", "amount"], [r[:3] for r in expected_txns(ledger)[:2]])
    res = sql(client, "SELECT count(*) AS n, sum(amount) AS total, avg(1.0)::float8 AS f FROM ledger.transactions")
    txns = ledger.company_txns(C1)
    assert res["rows"] == [[len(txns), str(sum(t.amount for t in txns)), 1.0]]
    res = sql(client, "SELECT 'NaN'::float8 AS nan, now() > '2000-01-01' AS b, '{\"a\": [1]}'::jsonb AS j, NULL AS z")
    assert res["rows"] == [["nan", True, {"a": [1]}, None]]


def test_sql_query_accepts_comments_trailing_semicolon_and_percent(client, ledger):
    assert sql(client, "  -- note; ignored\n/* block /* nested */ */ select 1 AS one ;  ")["rows"] == [[1]]
    assert sql(client, "WITH x AS (SELECT 2 AS two) SELECT * FROM x;")["rows"] == [[2]]
    res = sql(client, "SELECT name FROM ledger.vendors WHERE name LIKE '%Haul%'")
    assert res["rows"] == [["Swift Haul Logistics"]]


@pytest.mark.parametrize("tbl", ["ledger.companies", "ledger.planted_anomalies"])
def test_sql_query_cannot_read_protected_ledger_tables(client, ledger, tbl):
    assert sql_err(client, f"SELECT * FROM {tbl}").startswith("SQL error: permission denied for table")


def test_sql_query_cannot_read_any_app_table(client, ledger, conn):
    app_tables = [t for (t,) in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'app' ORDER BY 1")]
    assert {"tasks", "sandbox_entries", "runs"} <= set(app_tables)
    for tbl in app_tables:
        assert sql_err(client, f"SELECT * FROM app.{tbl}") == "SQL error: permission denied for schema app"


@pytest.mark.parametrize(
    "query",
    [
        # read another company's rows by switching app.company_id mid-statement
        "SELECT (SELECT set_config('app.company_id', 'company-903', true)),"
        " (SELECT count(*) FROM ledger.transactions WHERE company_id = 'company-903')",
        # switch back to the superuser role, then run a nested query that reads expected answers
        "SELECT set_config('role', 'scar', true), query_to_xml('SELECT body FROM app.tasks', true, false, '')",
        "SELECT set_config('role', 'scar', true),"
        " query_to_xml('SELECT * FROM ledger.planted_anomalies', true, false, '')",
        "SELECT set_config('statement_timeout', '0', false)",
    ],
)
def test_sql_query_set_config_escapes_fail(client, ledger, query):
    assert sql_err(client, query) == "SQL error: permission denied for function set_config"


def test_harden_sql_tool_is_idempotent(conn):
    harden_sql_tool(conn)
    harden_sql_tool(conn)
    privilege = "SELECT has_function_privilege('scar_sql_tool', 'pg_catalog.set_config(text,text,boolean)', 'EXECUTE')"
    assert conn.execute(privilege).fetchone() == (False,)


@pytest.mark.parametrize(
    "query",
    [
        "DELETE FROM ledger.transactions",
        "update ledger.accounts SET name = 'x'",
        "INSERT INTO ledger.vendors VALUES ('v', 'company-901', 'x')",
        "CREATE TABLE ledger.x (a int)",
        "SET ROLE scar",
        "RESET ROLE",
        "COPY ledger.transactions TO STDOUT",
        "EXPLAIN SELECT 1",
        "(SELECT 1)",
        "selection",
        "/* /* nested */ SELECT 1 */ DELETE FROM ledger.transactions",
        "-- SELECT 1\nDELETE FROM ledger.transactions",
        "",
        "  ;",
    ],
)
def test_sql_query_rejects_non_select(client, ledger, query):
    assert sql_err(client, query) == "sql must be a single SELECT statement (starting with SELECT or WITH)"


def test_sql_query_rejects_bad_sql_argument(client, ledger):
    assert sql_err(client, "/* unterminated SELECT 1") == "sql has an unterminated /* comment"
    assert err(client, "sql_query", {"sql": 5}) == "sql must be a string"


@pytest.mark.parametrize(
    "query",
    ["SELECT 1; SELECT 2", "SELECT 1;;", "SELECT 1; DELETE FROM ledger.transactions", "SELECT ';'", "SELECT 1; -- x"],
)
def test_sql_query_rejects_multiple_statements(client, ledger, query):
    assert sql_err(client, query) == "sql must be a single statement: only one trailing semicolon is allowed"


@pytest.mark.parametrize(
    "query",
    [
        "WITH d AS (DELETE FROM ledger.transactions RETURNING *) SELECT count(*) FROM d",
        "WITH u AS (UPDATE ledger.accounts SET name = 'x' RETURNING *) SELECT * FROM u",
        "WITH i AS (INSERT INTO ledger.vendors VALUES ('v', 'company-901', 'x') RETURNING *) SELECT * FROM i",
        "SELECT * INTO TEMP stolen FROM ledger.accounts",
        "SELECT * FROM ledger.accounts FOR UPDATE",
        "SELECT lo_create(0)",
    ],
)
def test_sql_query_rejects_writes(client, ledger, conn, query):
    before = conn.execute(
        "SELECT (SELECT count(*) FROM ledger.transactions), (SELECT count(*) FROM ledger.vendors)"
    ).fetchone()
    assert sql_err(client, query).startswith("SQL error: ")
    after = conn.execute(
        "SELECT (SELECT count(*) FROM ledger.transactions), (SELECT count(*) FROM ledger.vendors)"
    ).fetchone()
    assert after == before
    assert conn.execute("SELECT count(*) FROM ledger.accounts WHERE name = 'x'").fetchone() == (0,)


def test_sql_query_reports_sql_errors(client, ledger):
    assert sql_err(client, "SELECT FROM WHERE") == 'SQL error: syntax error at or near "WHERE"'
    assert sql_err(client, "SELECT nope FROM ledger.accounts") == 'SQL error: column "nope" does not exist'
    assert sql_err(client, "SELECT 1/0") == "SQL error: division by zero"
    assert sql_err(client, "SELECT 'infinity'::date").startswith("SQL error: ")


def test_sql_query_caps_rows_at_200(client, ledger):
    res = sql(client, "SELECT g FROM generate_series(1, 250) AS g ORDER BY g")
    assert res == table(["g"], [(i,) for i in range(1, 201)], has_more=True)
    assert sql(client, "SELECT g FROM generate_series(1, 200) AS g")["has_more"] is False
    big = sql(client, "SELECT a.txn_id, b.txn_id FROM ledger.transactions a, ledger.transactions b")
    assert big["row_count"] == 200 and big["has_more"] is True


def test_sql_query_times_out_after_5_seconds(client, ledger):
    start = time.monotonic()
    assert sql_err(client, "SELECT pg_sleep(6)") == "SQL error: query canceled after the 5 s time limit"
    assert time.monotonic() - start < 6


def test_sql_query_leaves_no_role_or_settings_on_the_connection(conn, ledger):
    def run(query):
        return execute(conn, ToolExecuteRequest(run_id=RUN, company_id=C1, name="sql_query", arguments={"sql": query}))

    state_sql = (
        "SELECT current_user = session_user, current_setting('transaction_read_only'),"
        " current_setting('app.company_id', true), current_setting('statement_timeout')"
    )
    before = conn.execute(state_sql).fetchone()
    assert before[:2] == (True, "off")
    inside = run("SELECT current_user, current_setting('transaction_read_only'), current_setting('statement_timeout')")
    assert inside.result["rows"] == [["scar_sql_tool", "on", "5s"]]
    assert run("SELECT 1/0").ok is False
    assert conn.execute(state_sql).fetchone() == (True, "off", before[2] or "", before[3])


def test_pooled_connection_can_write_after_sql_query(client, ledger, conn):
    assert sql_err(client, "SELECT 1/0").startswith("SQL error")
    assert sql(client, "SELECT 1")["rows"] == [[1]]
    entry = {"entry_date": "2026-03-31", "lines": [debit("acct-insurance", 10), credit("acct-checking", 10)]}
    assert ok(client, "create_journal_entry", entry)["entry_id"].startswith("entry_")
    assert sandbox_count(conn) == 1


# ---------- create_journal_entry ----------


def debit(acct, amount, company=C1, **extra) -> dict:
    return {"account_id": f"{company}:{acct}", "debit": amount, **extra}


def credit(acct, amount, company=C1, **extra) -> dict:
    return {"account_id": f"{company}:{acct}", "credit": amount, **extra}


def test_create_journal_entry_stores_the_exact_format(client, ledger, conn):
    before = datetime.now(timezone.utc)
    lines = [
        debit("acct-prepaid-expenses", 1200, memo="annual policy"),
        credit("acct-checking", 1199.5),
        credit("acct-accounts-payable", 0.5, debit=0),
    ]
    result = ok(client, "create_journal_entry", {"entry_date": "2026-03-31", "lines": lines})
    entry_id = result["entry_id"]
    assert result == {"entry_id": entry_id} and re.fullmatch(r"entry_[0-9a-f]{32}", entry_id)
    rows = conn.execute(
        "SELECT entry_id, run_id, company_id, entry_date, lines, created_at FROM app.sandbox_entries"
    ).fetchall()
    assert len(rows) == 1
    stored_id, run_id, company_id, entry_date, stored_lines, created_at = rows[0]
    assert (stored_id, run_id, company_id, entry_date) == (entry_id, RUN, C1, date(2026, 3, 31))
    assert stored_lines == [
        {
            "account_id": "company-901:acct-prepaid-expenses",
            "debit": "1200.00",
            "credit": "0.00",
            "memo": "annual policy",
        },
        {"account_id": "company-901:acct-checking", "debit": "0.00", "credit": "1199.50", "memo": None},
        {"account_id": "company-901:acct-accounts-payable", "debit": "0.00", "credit": "0.50", "memo": None},
    ]
    assert stored_lines[0] == JournalLine(
        account_id="company-901:acct-prepaid-expenses",
        debit=Decimal("1200.00"),
        credit=Decimal("0.00"),
        memo="annual policy",
    ).model_dump(mode="json")
    assert created_at.tzinfo is not None and before <= created_at <= datetime.now(timezone.utc)


def test_create_journal_entry_rejects_unbalanced(client, ledger, conn):
    lines = [debit("acct-insurance", 100), credit("acct-checking", 99.99)]
    message = err(client, "create_journal_entry", {"entry_date": "2026-03-31", "lines": lines})
    assert message == "entry does not balance: total debits 100.00 != total credits 99.99"
    lines = [debit("acct-insurance", 100), debit("acct-rent", 100)]
    assert "does not balance" in err(client, "create_journal_entry", {"entry_date": "2026-03-31", "lines": lines})
    assert sandbox_count(conn) == 0


def test_create_journal_entry_rejects_foreign_and_unknown_accounts(client, ledger, conn):
    for acct in ("company-902:acct-rent", "company-903:acct-rent", "company-901:acct-nope"):
        lines = [{"account_id": acct, "debit": 50}, credit("acct-checking", 50)]
        message = err(client, "create_journal_entry", {"entry_date": "2026-03-31", "lines": lines})
        assert message == f"unknown account_id for this company: {acct} (use get_accounts)"
    assert sandbox_count(conn) == 0


GOOD = credit("acct-checking", 100)


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (
            {"entry_date": "2026-02-30", "lines": [debit("acct-rent", 100), GOOD]},
            "entry_date must be a date formatted YYYY-MM-DD, got '2026-02-30'",
        ),
        (
            {"entry_date": "03/31/2026", "lines": [debit("acct-rent", 100), GOOD]},
            "entry_date must be a date formatted YYYY-MM-DD, got '03/31/2026'",
        ),
        (
            {"entry_date": 20260331, "lines": [debit("acct-rent", 100), GOOD]},
            "entry_date must be a date formatted YYYY-MM-DD, got 20260331",
        ),
        ({"entry_date": "2026-03-31", "lines": "two lines"}, "lines must be a list of at least 2 lines"),
        ({"entry_date": "2026-03-31", "lines": [debit("acct-rent", 0.0)]}, "lines must be a list of at least 2 lines"),
        (
            {"entry_date": "2026-03-31", "lines": ["acct-rent", GOOD]},
            "line 1 must be an object with account_id and a debit or credit",
        ),
        (
            {"entry_date": "2026-03-31", "lines": [{"debit": 100}, GOOD]},
            "line 1: account_id must be a non-empty string",
        ),
        (
            {"entry_date": "2026-03-31", "lines": [{"account_id": "", "debit": 100}, GOOD]},
            "line 1: account_id must be a non-empty string",
        ),
        (
            {"entry_date": "2026-03-31", "lines": [debit("acct-rent", 100, total=1), GOOD]},
            "line 1: unknown key(s): total; allowed: account_id, debit, credit, memo",
        ),
        (
            {"entry_date": "2026-03-31", "lines": [debit("acct-rent", 100, credit=100), GOOD]},
            "line 1: exactly one of debit or credit must be greater than 0",
        ),
        (
            {"entry_date": "2026-03-31", "lines": [debit("acct-rent", 0), GOOD]},
            "line 1: exactly one of debit or credit must be greater than 0",
        ),
        (
            {"entry_date": "2026-03-31", "lines": [{"account_id": "company-901:acct-rent"}, GOOD]},
            "line 1: exactly one of debit or credit must be greater than 0",
        ),
        (
            {"entry_date": "2026-03-31", "lines": [debit("acct-rent", -100), GOOD]},
            "line 1: debit must not be negative, got -100",
        ),
        (
            {"entry_date": "2026-03-31", "lines": [debit("acct-rent", 100.005), GOOD]},
            "line 1: debit must have at most 2 decimal places, got 100.005",
        ),
        (
            {"entry_date": "2026-03-31", "lines": [debit("acct-rent", "100"), GOOD]},
            "line 1: debit must be a number, got '100'",
        ),
        (
            {"entry_date": "2026-03-31", "lines": [debit("acct-rent", True), GOOD]},
            "line 1: debit must be a number, got True",
        ),
        (
            {"entry_date": "2026-03-31", "lines": [debit("acct-rent", 1e12), GOOD]},
            "line 1: debit must be less than 1000000000000, got 1000000000000.0",
        ),
        (
            {"entry_date": "2026-03-31", "lines": [debit("acct-rent", 100), credit("acct-checking", 100, memo=5)]},
            "line 2: memo must be a string",
        ),
        (
            {"entry_date": "2026-03-31", "lines": [debit("acct-rent", 100), GOOD], "memo": "x"},
            "unknown argument(s): memo; allowed: entry_date, lines",
        ),
    ],
)
def test_create_journal_entry_rejects_invalid_input(client, ledger, conn, args, message):
    assert err(client, "create_journal_entry", args) == message
    assert sandbox_count(conn) == 0
