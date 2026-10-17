"""Verifier (SPEC 9.3): pass/fail for a run, by code only (R5). It never calls an LLM."""

from decimal import Decimal, InvalidOperation
from typing import Any

import psycopg
from scar_schemas.models import AnswerType, ExpectedAnswer, JournalLine, Task, VerifyRequest, VerifyResult

JOURNAL_TOLERANCE = Decimal("0.01")

# (entry_date, sorted((account_id, debit, credit))), the comparison key from SPEC 9.3
EntryKey = tuple[str, list[tuple[str, Decimal, Decimal]]]


def verify(conn: psycopg.Connection, task: Task, req: VerifyRequest) -> VerifyResult:
    if req.stop_reason != "submitted":
        return VerifyResult(passed=False, detail=f"not submitted: {req.stop_reason}")
    answer = req.final_answer or {}
    if task.expected.answer_type == AnswerType.number:
        return _verify_number(answer, task.expected)
    if task.expected.answer_type == AnswerType.id_set:
        return _verify_ids(answer, task.expected)
    return _verify_journal(conn, task, req.run_id, answer)


def _verify_number(answer: dict[str, Any], expected: ExpectedAnswer) -> VerifyResult:
    """Pass if |x - expected| <= tolerance. x may be an int, float or numeric string; bools are rejected."""
    raw = answer.get("number")
    if raw is None:
        return _fail("number: missing from final_answer")
    x = _decimal(raw)
    if x is None:
        return _fail(f"number: not a number: {raw!r}")
    return VerifyResult(
        passed=abs(x - expected.number) <= expected.tolerance, detail=f"number: expected {expected.number}, got {x}"
    )


def _verify_ids(answer: dict[str, Any], expected: ExpectedAnswer) -> VerifyResult:
    """Pass on set equality; order and duplicates are ignored."""
    ids = answer.get("ids")
    if ids is None:
        return _fail("ids: missing from final_answer")
    if not _is_str_list(ids):
        return _fail(f"ids: expected a list of strings, got {ids!r}")
    want, got = set(expected.id_set), set(ids)
    missing, extra = sorted(want - got), sorted(got - want)
    return VerifyResult(
        passed=not missing and not extra,
        detail=f"ids: expected {len(want)}, got {len(got)} distinct; missing {missing}; extra {extra}",
    )


def _verify_journal(conn: psycopg.Connection, task: Task, run_id: str, answer: dict[str, Any]) -> VerifyResult:
    """Submitted entries must be this run's, for the task's company, and balanced; then they must match the
    expected entries one-to-one (dates and accounts exact, amounts within 0.01, memos ignored). Repeated IDs
    name the same entry once."""
    entry_ids = answer.get("entry_ids")
    if entry_ids is None:
        return _fail("entry_ids: missing from final_answer")
    if not _is_str_list(entry_ids):
        return _fail(f"entry_ids: expected a list of strings, got {entry_ids!r}")
    ids = sorted(set(entry_ids))
    rows = {
        row[0]: row
        for row in conn.execute(
            "SELECT entry_id, run_id, company_id, entry_date, lines FROM app.sandbox_entries"
            " WHERE entry_id = ANY(%s) ORDER BY entry_id",
            (ids,),
        ).fetchall()
    }
    problems = [
        f"{label} {bad}"
        for label, bad in (
            ("unknown", [i for i in ids if i not in rows]),
            ("from another run", [i for i in ids if i in rows and rows[i][1] != run_id]),
            ("from another company", [i for i in ids if i in rows and rows[i][2] != task.company_id]),
        )
        if bad
    ]
    if problems:
        return _fail("entry_ids: " + "; ".join(problems))

    submitted: list[EntryKey] = []
    for entry_id in ids:
        lines = [JournalLine.model_validate(line) for line in rows[entry_id][4]]
        debits, credits = sum(line.debit for line in lines), sum(line.credit for line in lines)
        if debits != credits:
            return _fail(f"entry {entry_id} does not balance: debits {debits}, credits {credits}")
        submitted.append(_entry_key(rows[entry_id][3].isoformat(), lines))
    expected = [_entry_key(spec.entry_date, spec.lines) for spec in task.expected.journal_entries]

    owner = _match(submitted, expected)
    detail = f"journal_entry: matched {len(owner)} of {len(expected)} expected entries"
    if len(owner) == len(expected) == len(submitted):
        return VerifyResult(passed=True, detail=detail)
    matched = set(owner.values())
    extra = [f"{ids[s]} {_fmt(submitted[s])}" for s in range(len(submitted)) if s not in matched]
    missing = [_fmt(expected[e]) for e in range(len(expected)) if e not in owner]
    return _fail(f"{detail}; unmatched submitted: {extra}; unmatched expected: {missing}")


def _entry_key(entry_date: str, lines: list[JournalLine]) -> EntryKey:
    return entry_date, sorted((line.account_id, line.debit, line.credit) for line in lines)


def _same_entry(a: EntryKey, b: EntryKey) -> bool:
    return (
        a[0] == b[0]
        and len(a[1]) == len(b[1])
        and all(
            x[0] == y[0] and abs(x[1] - y[1]) <= JOURNAL_TOLERANCE and abs(x[2] - y[2]) <= JOURNAL_TOLERANCE
            for x, y in zip(a[1], b[1], strict=True)
        )
    )


def _match(submitted: list[EntryKey], expected: list[EntryKey]) -> dict[int, int]:
    """Maximum one-to-one matching {expected index: submitted index} (augmenting paths), so two entries that both
    fit within the tolerance can never make a correct submission fail the multiset comparison."""
    owner: dict[int, int] = {}

    def place(s: int, seen: set[int]) -> bool:
        for e, exp in enumerate(expected):
            if e not in seen and _same_entry(submitted[s], exp):
                seen.add(e)
                if e not in owner or place(owner[e], seen):
                    owner[e] = s
                    return True
        return False

    for s in range(len(submitted)):
        place(s, set())
    return owner


def _fmt(key: EntryKey) -> str:
    return f"{key[0]} " + ", ".join(f"({a}, {d}, {c})" for a, d, c in key[1])


def _decimal(x: Any) -> Decimal | None:
    if isinstance(x, bool) or not isinstance(x, int | float | str):
        return None
    try:
        d = Decimal(str(x))
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def _is_str_list(x: Any) -> bool:
    return isinstance(x, list) and all(isinstance(i, str) for i in x)


def _fail(detail: str) -> VerifyResult:
    return VerifyResult(passed=False, detail=detail)
