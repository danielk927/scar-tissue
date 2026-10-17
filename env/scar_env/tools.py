"""The five env tools behind POST /tools/execute (SPEC 8.4, P1-04).

The company comes only from the request; tools never take company_id. A bad tool name or bad arguments raise
ToolError, returned to the model as ok=false (HTTP 200). psycopg errors are infrastructure failures (HTTP 503).

Results are plain JSON (Decimals and dates as strings) and deterministic. The four list tools return one table
shape, column-oriented to keep results short (the agent cuts them at TOOL_RESULT_MAX_CHARS):
    {"columns": [...], "has_more": bool, "row_count": n, "rows": [[...], ...]}
Keys are in sorted order, so `has_more` and `row_count` come before `rows` even after the agent's sort_keys
serialization and truncation. `has_more` is true when a row cap (get_transactions `limit`, sql_query 200) cut the
result; it is always false for get_accounts and get_vendors. Columns:
- get_accounts: account_id, name, account_type
- get_vendors: vendor_id, name
- get_transactions: txn_id, txn_date, amount, account_id, vendor_id, description, txn_type
- sql_query: whatever the statement selects
create_journal_entry returns {"entry_id": "entry_<hex>"}.
"""

import math
import re
from collections.abc import Callable
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.types.json import Jsonb
from scar_schemas.constants import TOOLS_VERSION
from scar_schemas.ids import new_id
from scar_schemas.models import JournalLine, ToolExecuteRequest, ToolExecuteResult
from scar_schemas.openai_format import load_tools

TXN_DEFAULT_LIMIT = 100
TXN_MAX_LIMIT = 500
SQL_MAX_ROWS = 200
MAX_AMOUNT = Decimal("1000000000000")  # journal amounts must fit NUMERIC(14,2) (SPEC 7.4)
CENT = Decimal("0.01")

_PARAMS = {t["function"]["name"]: t["function"]["parameters"] for t in load_tools(TOOLS_VERSION)}
_LINE_KEYS = list(_PARAMS["create_journal_entry"]["properties"]["lines"]["items"]["properties"])
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_SELECT_RE = re.compile(r"(select|with)\b", re.IGNORECASE)
_LINE_COMMENT_RE = re.compile(r"--[^\n\r]*")


class ToolError(Exception):
    """Bad tool name or arguments; the message is shown to the model with ok=false."""


def harden_sql_tool(conn: psycopg.Connection) -> None:
    """Revoke set_config from PUBLIC so a sql_query statement cannot change app.company_id or switch back to the
    superuser role. Idempotent; superusers (all our services) are unaffected. Fails loudly if it did not apply."""
    conn.execute("REVOKE EXECUTE ON FUNCTION pg_catalog.set_config(text, text, boolean) FROM PUBLIC")
    (allowed,) = conn.execute(
        "SELECT has_function_privilege('scar_sql_tool', 'pg_catalog.set_config(text, text, boolean)', 'EXECUTE')"
    ).fetchone()
    if allowed:
        raise RuntimeError("scar_sql_tool can still execute set_config; sql_query would be unsafe")


def execute(conn: psycopg.Connection, req: ToolExecuteRequest) -> ToolExecuteResult:
    """Run one tool for req.company_id (the caller checks the company exists). Invalid input changes nothing."""
    try:
        if req.name not in _TOOLS:
            if req.name == "submit_answer":
                raise ToolError("submit_answer ends the task and is handled by the agent, not executed here")
            raise ToolError(f"unknown tool {req.name!r}; available tools: {', '.join(_PARAMS)}")
        _check_arg_names(req.name, req.arguments)
        with conn.transaction():
            conn.execute("SET LOCAL statement_timeout = '10s'")  # tool timeout (SPEC 7.4)
            result = _TOOLS[req.name](conn, req)
    except ToolError as e:
        return ToolExecuteResult(ok=False, error=str(e))
    return ToolExecuteResult(ok=True, result=result)


def get_accounts(conn: psycopg.Connection, req: ToolExecuteRequest) -> dict:
    rows = conn.execute(
        "SELECT account_id, name, account_type FROM ledger.accounts WHERE company_id = %s"
        ' ORDER BY account_id COLLATE "C"',
        (req.company_id,),
    ).fetchall()
    return _table(["account_id", "name", "account_type"], rows, has_more=False)


def get_vendors(conn: psycopg.Connection, req: ToolExecuteRequest) -> dict:
    """name_contains: case-insensitive substring; % and _ are literal characters."""
    rows = conn.execute(
        "SELECT vendor_id, name FROM ledger.vendors WHERE company_id = %(c)s"
        " AND (%(q)s::text IS NULL OR strpos(lower(name), lower(%(q)s)) > 0)"
        ' ORDER BY vendor_id COLLATE "C"',
        {"c": req.company_id, "q": _opt_str(req.arguments, "name_contains")},
    ).fetchall()
    return _table(["vendor_id", "name"], rows, has_more=False)


def get_transactions(conn: psycopg.Connection, req: ToolExecuteRequest) -> dict:
    """All filters optional and inclusive; ordered by txn_date then txn_id; at most `limit` rows (default 100)."""
    args = req.arguments
    limit = args.get("limit")
    if limit is None:
        limit = TXN_DEFAULT_LIMIT
    elif isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= TXN_MAX_LIMIT:
        raise ToolError(f"limit must be an integer from 1 to {TXN_MAX_LIMIT}, got {limit!r}")
    params = {
        "c": req.company_id,
        "start": _opt_date(args, "start_date"),
        "end": _opt_date(args, "end_date"),
        "acct": _opt_str(args, "account_id"),
        "vend": _opt_str(args, "vendor_id"),
        "min": _opt_number(args, "min_amount"),
        "max": _opt_number(args, "max_amount"),
        "lim": limit + 1,
    }
    rows = conn.execute(
        "SELECT txn_id, txn_date, amount, account_id, vendor_id, description, txn_type FROM ledger.transactions"
        " WHERE company_id = %(c)s"
        " AND (%(start)s::date IS NULL OR txn_date >= %(start)s)"
        " AND (%(end)s::date IS NULL OR txn_date <= %(end)s)"
        " AND (%(acct)s::text IS NULL OR account_id = %(acct)s)"
        " AND (%(vend)s::text IS NULL OR vendor_id = %(vend)s)"
        " AND (%(min)s::numeric IS NULL OR amount >= %(min)s)"
        " AND (%(max)s::numeric IS NULL OR amount <= %(max)s)"
        ' ORDER BY txn_date, txn_id COLLATE "C" LIMIT %(lim)s',
        params,
    ).fetchall()
    columns = ["txn_id", "txn_date", "amount", "account_id", "vendor_id", "description", "txn_type"]
    return _table(columns, rows[:limit], has_more=len(rows) > limit)


def sql_query(conn: psycopg.Connection, req: ToolExecuteRequest) -> dict:
    """One SELECT/WITH statement, read-only, as role scar_sql_tool (row-level security limits it to the company),
    5 s statement timeout, at most 200 rows. Always rolled back, so the role and settings never outlive the call."""
    sql = _check_sql(req.arguments["sql"])
    with conn.transaction(force_rollback=True):
        conn.execute("SET TRANSACTION READ ONLY")
        # Set the company before switching role: scar_sql_tool cannot call set_config (harden_sql_tool).
        conn.execute("SELECT set_config('app.company_id', %s, true)", (req.company_id,))
        conn.execute("SET LOCAL statement_timeout = '5s'")
        conn.execute("SET LOCAL ROLE scar_sql_tool")
        try:
            # A server-side cursor computes only the rows fetched, so huge results never reach this process.
            with conn.cursor(name="sql_query") as cur:
                cur.execute(sql)
                rows = cur.fetchmany(SQL_MAX_ROWS + 1)
                columns = [c.name for c in cur.description or []]
        except psycopg.Error as e:
            if conn.closed:
                raise
            if isinstance(e, psycopg.errors.QueryCanceled):
                raise ToolError("SQL error: query canceled after the 5 s time limit") from e
            raise ToolError(f"SQL error: {e.diag.message_primary or e}") from e
    return _table(columns, rows[:SQL_MAX_ROWS], has_more=len(rows) > SQL_MAX_ROWS)


def create_journal_entry(conn: psycopg.Connection, req: ToolExecuteRequest) -> dict:
    """Validate, then store one balanced entry in app.sandbox_entries. Amounts are stored with 2 decimals."""
    args = req.arguments
    entry_date = _date(args["entry_date"], "entry_date")
    raw_lines = args["lines"]
    if not isinstance(raw_lines, list) or len(raw_lines) < 2:
        raise ToolError("lines must be a list of at least 2 lines")
    lines = [_journal_line(n, raw) for n, raw in enumerate(raw_lines, 1)]
    debits, credits = sum(line.debit for line in lines), sum(line.credit for line in lines)
    if debits != credits:
        raise ToolError(f"entry does not balance: total debits {debits} != total credits {credits}")
    accounts = sorted({line.account_id for line in lines})
    known = conn.execute(
        "SELECT account_id FROM ledger.accounts WHERE company_id = %s AND account_id = ANY(%s) ORDER BY account_id",
        (req.company_id, accounts),
    ).fetchall()
    unknown = sorted(set(accounts) - {a for (a,) in known})
    if unknown:
        raise ToolError(f"unknown account_id for this company: {', '.join(unknown)} (use get_accounts)")
    entry_id = new_id("entry")
    conn.execute(
        "INSERT INTO app.sandbox_entries (entry_id, run_id, company_id, entry_date, lines, created_at)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (
            entry_id,
            req.run_id,
            req.company_id,
            entry_date,
            Jsonb([line.model_dump(mode="json") for line in lines]),
            datetime.now(timezone.utc),
        ),
    )
    return {"entry_id": entry_id}


_TOOLS: dict[str, Callable[[psycopg.Connection, ToolExecuteRequest], dict]] = {
    "get_accounts": get_accounts,
    "get_vendors": get_vendors,
    "get_transactions": get_transactions,
    "sql_query": sql_query,
    "create_journal_entry": create_journal_entry,
}


def _check_arg_names(name: str, args: dict[str, Any]) -> None:
    """Unknown keys and missing required keys, per tools_v1.json. Optional arguments given as null count as absent."""
    allowed = _PARAMS[name]["properties"]
    unknown = sorted(set(args) - set(allowed))
    if unknown:
        raise ToolError(f"unknown argument(s): {', '.join(unknown)}; allowed: {', '.join(allowed) or 'none'}")
    missing = [key for key in _PARAMS[name]["required"] if args.get(key) is None]
    if missing:
        raise ToolError(f"missing required argument(s): {', '.join(missing)}")


def _check_sql(sql: Any) -> str:
    """The statement to run: leading comments removed, one trailing semicolon allowed and dropped."""
    if not isinstance(sql, str):
        raise ToolError("sql must be a string")
    body = _strip_leading_comments(sql).rstrip().removesuffix(";")
    if not _SELECT_RE.match(body):
        raise ToolError("sql must be a single SELECT statement (starting with SELECT or WITH)")
    if ";" in body:
        raise ToolError("sql must be a single statement: only one trailing semicolon is allowed")
    return body


def _strip_leading_comments(sql: str) -> str:
    """Drop leading whitespace, -- line comments and (nestable, as in Postgres) /* block comments */."""
    s = sql.lstrip()
    while s.startswith(("--", "/*")):
        if s.startswith("--"):
            s = s[_LINE_COMMENT_RE.match(s).end() :]
        else:
            depth, i = 1, 2
            while depth:
                if i >= len(s):
                    raise ToolError("sql has an unterminated /* comment")
                step = s[i : i + 2]
                depth += 1 if step == "/*" else -1 if step == "*/" else 0
                i += 2 if step in ("/*", "*/") else 1
            s = s[i:]
        s = s.lstrip()
    return s


def _journal_line(n: int, raw: Any) -> JournalLine:
    where = f"line {n}"
    if not isinstance(raw, dict):
        raise ToolError(f"{where} must be an object with account_id and a debit or credit")
    unknown = sorted(set(raw) - set(_LINE_KEYS))
    if unknown:
        raise ToolError(f"{where}: unknown key(s): {', '.join(unknown)}; allowed: {', '.join(_LINE_KEYS)}")
    account_id, memo = raw.get("account_id"), raw.get("memo")
    if not isinstance(account_id, str) or not account_id:
        raise ToolError(f"{where}: account_id must be a non-empty string")
    if memo is not None and not isinstance(memo, str):
        raise ToolError(f"{where}: memo must be a string")
    debit, credit = _amount(raw.get("debit"), f"{where}: debit"), _amount(raw.get("credit"), f"{where}: credit")
    if (debit > 0) == (credit > 0):
        raise ToolError(f"{where}: exactly one of debit or credit must be greater than 0")
    return JournalLine(account_id=account_id, debit=debit, credit=credit, memo=memo)


def _amount(value: Any, what: str) -> Decimal:
    if value is None:
        return Decimal("0.00")
    amount = _number(value, what)
    if amount < 0:
        raise ToolError(f"{what} must not be negative, got {value!r}")
    if amount >= MAX_AMOUNT:
        raise ToolError(f"{what} must be less than {MAX_AMOUNT}, got {value!r}")
    if amount != amount.quantize(CENT):
        raise ToolError(f"{what} must have at most 2 decimal places, got {value!r}")
    return amount.quantize(CENT)


def _number(value: Any, what: str) -> Decimal:
    """A JSON number as Decimal(str(x)) (SPEC 7.4); bools and strings are rejected."""
    number = None if isinstance(value, bool) or not isinstance(value, int | float) else Decimal(str(value))
    if number is None or not number.is_finite():
        raise ToolError(f"{what} must be a number, got {value!r}")
    return number


def _date(value: Any, what: str) -> date:
    if isinstance(value, str) and _DATE_RE.fullmatch(value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise ToolError(f"{what} must be a date formatted YYYY-MM-DD, got {value!r}")


def _opt_date(args: dict[str, Any], key: str) -> date | None:
    return None if args.get(key) is None else _date(args[key], key)


def _opt_number(args: dict[str, Any], key: str) -> Decimal | None:
    return None if args.get(key) is None else _number(args[key], key)


def _opt_str(args: dict[str, Any], key: str) -> str | None:
    value = args.get(key)
    if value is not None and not isinstance(value, str):
        raise ToolError(f"{key} must be a string, got {value!r}")
    return value


def _table(columns: list[str], rows: list[tuple], has_more: bool) -> dict:
    return {
        "columns": columns,
        "has_more": has_more,
        "row_count": len(rows),
        "rows": [[_json_value(v) for v in row] for row in rows],
    }


def _json_value(v: Any) -> Any:
    """Plain JSON for any value psycopg returns: Decimals, dates and other non-JSON types become strings."""
    if v is None or isinstance(v, bool | int | str):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else str(v)
    if isinstance(v, date | datetime | time):
        return v.isoformat()
    if isinstance(v, list | tuple):
        return [_json_value(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _json_value(x) for k, x in v.items()}
    return str(v)
