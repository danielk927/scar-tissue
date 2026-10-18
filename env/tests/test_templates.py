"""Template catalog (P1-06): SQL answers vs independent Python answers on the synthetic ledger, samplers, phrasings."""

import calendar
import random
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from statistics import median

import pytest
from scar_schemas.models import AnswerType, Origin, TaskFamily

from scar_env.tasks import MAX_TRIES, sample_task
from scar_env.templates import MAX_IDS, TEMPLATES, ParamError, get_template, load_company
from scar_env.templates.base import TIER_DAYS, id_set_answer, placeholder_names

from .synthetic import SyntheticLedger, Txn

TRAIN = ("company-901", "company-902")
COMPANIES = (*TRAIN, "company-903")
DOCS = Path(__file__).resolve().parents[2] / "docs" / "TEMPLATES.md"
PERIOD_AGGREGATIONS = [
    t for t in TEMPLATES.values() if t.family == TaskFamily.aggregation and "start_date" in t.Params.model_fields
]


def _d(s: str) -> date:
    return date.fromisoformat(s)


def reference(ledger, template_id: str, cid: str, p: dict):
    """The expected answer computed in Python from the synthetic rows, independently of the SQL under test."""
    txns = ledger.company_txns(cid)
    types = {a: t for a, _, _, t in ledger.accounts}
    period = [t for t in txns if "start_date" in p and _d(p["start_date"]) <= t.txn_date <= _d(p["end_date"])]
    zero = Decimal(0)
    if template_id == "agg.total_by_account_period":
        return sum((t.amount for t in period if t.account_id == p["account_id"]), zero)
    if template_id == "agg.total_by_vendor_period":
        return sum((t.amount for t in period if t.vendor_id == p["vendor_id"]), zero)
    if template_id == "agg.count_by_account_period":
        return Decimal(sum(t.account_id == p["account_id"] for t in period))
    if template_id == "agg.top_vendor_by_spend":
        spend = defaultdict(Decimal)
        for t in period:
            if t.vendor_id:
                spend[t.vendor_id] += t.amount
        return sorted(v for v, s in spend.items() if s == max(spend.values()))
    if template_id == "agg.mom_change_account":
        y, m = map(int, p["month"].split("-"))
        prev = (y, m - 1) if m > 1 else (y - 1, 12)

        def month_total(ym):
            return sum(
                (
                    t.amount
                    for t in txns
                    if t.account_id == p["account_id"] and (t.txn_date.year, t.txn_date.month) == ym
                ),
                zero,
            )

        return month_total((y, m)) - month_total(prev)
    if template_id == "agg.share_of_expense":
        expense = [t for t in period if types[t.account_id] == "expense"]
        part = sum((t.amount for t in expense if t.account_id == p["account_id"]), zero)
        return (100 * part / sum(t.amount for t in expense)).quantize(Decimal("0.01"), ROUND_HALF_UP)
    if template_id == "anom.duplicate_payments":
        same = defaultdict(list)  # (vendor, amount) -> transactions, so the pair scan stays small on real data
        for t in period:
            if t.vendor_id:
                same[(t.vendor_id, t.amount)].append(t)
        return sorted(
            {
                b.txn_id
                for group in same.values()
                for a in group
                for b in group
                if (a.txn_date, a.txn_id) < (b.txn_date, b.txn_id) and (b.txn_date - a.txn_date).days <= 3
            }
        )
    if template_id == "anom.round_amounts_over":
        return sorted(t.txn_id for t in period if t.amount >= Decimal(p["threshold"]) and t.amount % 100 == 0)
    if template_id == "anom.vendor_outliers":
        m = median(t.amount for t in txns if t.vendor_id == p["vendor_id"])
        return sorted(t.txn_id for t in period if t.vendor_id == p["vendor_id"] and t.amount > 3 * m)
    if template_id == "book.accrual":
        a = Decimal(p["amount"])
        return [(p["entry_date"], [(p["expense_account_id"], a, 0), (p["liability_account_id"], 0, a)])]
    if template_id == "book.prepaid_amortization":
        total, n = Decimal(p["total"]), p["months"]
        per = (total / n).quantize(Decimal("0.01"), ROUND_HALF_UP)
        y, m = map(int, p["start_month"].split("-"))
        out = []
        for i in range(n):
            yy, mm = y + (m - 1 + i) // 12, (m - 1 + i) % 12 + 1
            amount = per if i < n - 1 else total - per * (n - 1)
            last_day = date(yy, mm, calendar.monthrange(yy, mm)[1]).isoformat()
            out.append((last_day, [(p["expense_account_id"], amount, 0), (p["prepaid_account_id"], 0, amount)]))
        return out
    if template_id == "book.reclassify":
        t = next(t for t in txns if t.txn_id == p["txn_id"])
        return [(t.txn_date.isoformat(), [(p["to_account_id"], t.amount, 0), (t.account_id, 0, t.amount)])]
    raise AssertionError(template_id)


def got(expected):
    if expected.answer_type == AnswerType.number:
        return expected.number
    if expected.answer_type == AnswerType.id_set:
        return expected.id_set
    return [(e.entry_date, [(x.account_id, x.debit, x.credit) for x in e.lines]) for e in expected.journal_entries]


def sampled(conn, template, company, seeds=range(4)) -> list[dict]:
    out = []
    for difficulty in template.difficulties:
        for seed in seeds:
            p = template.sample(conn, company, random.Random(seed), difficulty)
            if p is not None:
                out.append(template.validate(conn, company, p))
    return out


def reference_from_db(conn, template_id: str, cid: str, p: dict):
    """`reference` over the rows currently in the database (synthetic ledger plus rows a test inserted)."""
    rows = conn.execute(
        "SELECT txn_id, company_id, txn_date, amount, account_id, vendor_id, description, txn_type"
        " FROM ledger.transactions WHERE company_id = %s ORDER BY txn_id",
        (cid,),
    ).fetchall()
    accounts = conn.execute("SELECT account_id, company_id, name, account_type FROM ledger.accounts").fetchall()
    return reference(SyntheticLedger([], accounts, [], [Txn(*r) for r in rows], []), template_id, cid, p)


def insert_txns(conn, rows) -> None:
    """rows: (txn_id, company_id, txn_date, amount, account_id, vendor_id)."""
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO ledger.transactions VALUES (%s, %s, %s, %s, %s, %s, 'test row', 'bill')",
            [(i, c, d, Decimal(a), acct, v) for i, c, d, a, acct, v in rows],
        )


# ---------- catalog shape ----------


def test_catalog_matches_spec_order_and_shape():
    assert list(TEMPLATES) == [
        "agg.total_by_account_period",
        "agg.total_by_vendor_period",
        "agg.count_by_account_period",
        "agg.top_vendor_by_spend",
        "agg.mom_change_account",
        "agg.share_of_expense",
        "anom.duplicate_payments",
        "anom.round_amounts_over",
        "anom.vendor_outliers",
        "book.accrual",
        "book.prepaid_amortization",
        "book.reclassify",
    ]
    for t in TEMPLATES.values():
        spec = t.spec
        assert spec.template_id.startswith(
            {"aggregation": "agg.", "anomaly": "anom.", "bookkeeping": "book."}[t.family]
        )
        assert spec.description.endswith(".") and ". " not in spec.description, "description must be one sentence"
        schema = spec.params_schema
        assert schema["type"] == "object" and schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])
        assert len(spec.phrasings) >= 5 and len(set(spec.phrasings)) == len(spec.phrasings)
        assert t.difficulties[0] == spec.difficulty and list(t.difficulties) == sorted(set(t.difficulties))
        assert t.difficulties[-1] <= 3
    for family in TaskFamily:  # generation needs every difficulty in every family
        assert {d for t in TEMPLATES.values() if t.family == family for d in t.difficulties} == {1, 2, 3}


def test_get_template_rejects_unknown_ids():
    assert get_template("agg.share_of_expense") is TEMPLATES["agg.share_of_expense"]
    with pytest.raises(ParamError, match="unknown template_id: agg.nope"):
        get_template("agg.nope")


def test_docs_quote_every_template_sql_verbatim():
    text = DOCS.read_text()
    for t in TEMPLATES.values():
        assert f"### `{t.template_id}`" in text
        for sql in (*t.sql, getattr(t, "candidates_sql", "")):
            assert sql in text, f"docs/TEMPLATES.md is missing the SQL of {t.template_id}"


# ---------- answers ----------


@pytest.mark.parametrize("template_id", list(TEMPLATES))
def test_sql_answers_match_python_reference_on_sampled_params(conn, ledger, template_id):
    template = TEMPLATES[template_id]
    checked = 0
    for cid in COMPANIES:
        company = load_company(conn, cid)
        for p in sampled(conn, template, company):
            assert got(template.answer(conn, company, p).expected) == reference(ledger, template_id, cid, p), p
            checked += 1
    assert checked >= 12


HAND_CHECKED = [
    # 3 monthly rent bills of 2500.00
    (
        "agg.total_by_account_period",
        "company-901",
        {"account_id": "company-901:acct-rent", "start_date": "2026-01-01", "end_date": "2026-03-31"},
        Decimal("7500.00"),
    ),
    # quarterly insurance on Jan 1 and Apr 1
    (
        "agg.total_by_vendor_period",
        "company-902",
        {"vendor_id": "company-902:vend-insurer", "start_date": "2026-01-01", "end_date": "2026-06-29"},
        Decimal("2400.00"),
    ),
    (
        "agg.count_by_account_period",
        "company-901",
        {"account_id": "company-901:acct-rent", "start_date": "2026-01-01", "end_date": "2026-06-29"},
        Decimal(6),
    ),
    # January: rent 2500 beats insurance 1200 and four freight bills under 400 each
    (
        "agg.top_vendor_by_spend",
        "company-901",
        {"start_date": "2026-01-01", "end_date": "2026-01-31"},
        ["company-901:vend-landlord"],
    ),
    # insurance: 1200 in April, nothing in May; the change is negative
    (
        "agg.mom_change_account",
        "company-901",
        {"account_id": "company-901:acct-insurance", "month": "2026-05"},
        Decimal("-1200.00"),
    ),
    # 2026-01-01 has only rent 2500 and insurance 1200: 2500 / 3700 = 67.567...% -> 67.57
    (
        "agg.share_of_expense",
        "company-901",
        {"account_id": "company-901:acct-rent", "start_date": "2026-01-01", "end_date": "2026-01-01"},
        Decimal("67.57"),
    ),
    (
        "anom.duplicate_payments",
        "company-901",
        {"start_date": "2026-01-01", "end_date": "2026-06-29"},
        ["company-901:planted-dup-0001:1"],
    ),
    # rent (bill 1) and insurance (bill 2) on Jan 1; threshold is inclusive
    (
        "anom.round_amounts_over",
        "company-901",
        {"threshold": "1200.00", "start_date": "2026-01-01", "end_date": "2026-01-31"},
        ["company-901:bill-00001:1", "company-901:bill-00002:1"],
    ),
    (
        "anom.round_amounts_over",
        "company-901",
        {"threshold": "1200.01", "start_date": "2026-01-01", "end_date": "2026-01-31"},
        ["company-901:bill-00001:1"],
    ),
    # 40 regular bills (6 rent, 2 insurance, 6 utilities, 26 freight), then the 2950.37 freight outlier
    (
        "anom.vendor_outliers",
        "company-901",
        {"vendor_id": "company-901:vend-freight-vendor", "start_date": "2026-01-01", "end_date": "2026-06-29"},
        ["company-901:bill-00041:1"],
    ),
    (
        "book.accrual",
        "company-901",
        {
            "expense_account_id": "company-901:acct-utilities",
            "liability_account_id": "company-901:acct-accounts-payable",
            "amount": "500.00",
            "entry_date": "2026-03-31",
        },
        [("2026-03-31", [("company-901:acct-utilities", 500, 0), ("company-901:acct-accounts-payable", 0, 500)])],
    ),
    (
        "book.prepaid_amortization",
        "company-901",
        {
            "prepaid_account_id": "company-901:acct-prepaid-expenses",
            "expense_account_id": "company-901:acct-insurance",
            "total": "1000.00",
            "months": 3,
            "start_month": "2025-12",
        },
        [
            (d, [("company-901:acct-insurance", Decimal(a), 0), ("company-901:acct-prepaid-expenses", 0, Decimal(a))])
            for d, a in (("2025-12-31", "333.33"), ("2026-01-31", "333.33"), ("2026-02-28", "333.34"))
        ],
    ),
    (
        "book.reclassify",
        "company-901",
        {"txn_id": "company-901:bill-00001:1", "to_account_id": "company-901:acct-utilities"},
        [("2026-01-01", [("company-901:acct-utilities", 2500, 0), ("company-901:acct-rent", 0, 2500)])],
    ),
]


@pytest.mark.parametrize(("template_id", "cid", "params", "expected"), HAND_CHECKED)
def test_hand_checked_answers(conn, ledger, template_id, cid, params, expected):
    template = TEMPLATES[template_id]
    company = load_company(conn, cid)
    p = template.validate(conn, company, params)
    assert got(template.answer(conn, company, p).expected) == expected
    assert reference(ledger, template_id, cid, p) == expected


def test_duplicates_tie_break_chain_and_period_boundary(conn, ledger):
    """Same-day pairs: the larger txn_id is later. Both transactions must be inside the period."""
    conn.execute("INSERT INTO ledger.vendors VALUES ('company-901:vend-test', 'company-901', 'Test Vendor')")
    v, acct = "company-901:vend-test", "company-901:acct-freight"
    insert_txns(
        conn,
        [
            ("company-901:x-a:1", "company-901", date(2026, 2, 10), "777.77", acct, v),
            ("company-901:x-b:1", "company-901", date(2026, 2, 10), "777.77", acct, v),  # same day as x-a
            ("company-901:x-c:1", "company-901", date(2026, 2, 13), "777.77", acct, v),  # 3 days later
            ("company-901:x-d:1", "company-901", date(2026, 2, 17), "777.77", acct, v),  # 4 days after x-c
        ],
    )
    template, company = TEMPLATES["anom.duplicate_payments"], load_company(conn, "company-901")
    # The planted duplicate (2026-02-11) copies a 2026-02-09 bill, which is outside this period.
    inside = {"start_date": "2026-02-10", "end_date": "2026-02-28"}
    assert template.answer(conn, company, inside).expected.id_set == ["company-901:x-b:1", "company-901:x-c:1"]
    wider = {"start_date": "2026-02-09", "end_date": "2026-02-28"}
    assert template.answer(conn, company, wider).expected.id_set == [
        "company-901:planted-dup-0001:1",
        "company-901:x-b:1",
        "company-901:x-c:1",
    ]
    for p in (inside, wider):
        assert reference_from_db(conn, template.template_id, "company-901", p) == got(
            template.answer(conn, company, p).expected
        )


def test_vendor_outlier_median_is_the_mean_of_the_middle_two_and_strict(conn, ledger):
    conn.execute(
        "INSERT INTO ledger.vendors VALUES ('company-901:vend-a', 'company-901', 'Vendor A'),"
        " ('company-901:vend-b', 'company-901', 'Vendor B')"
    )
    acct = "company-901:acct-utilities"
    rows = []
    for vendor, amounts in (("vend-a", ("10", "20", "30", "75")), ("vend-b", ("10", "20", "30", "76"))):
        for i, amount in enumerate(amounts):
            rows.append(
                (
                    f"company-901:{vendor}-{i}:1",
                    "company-901",
                    date(2026, 3, 2 + i),
                    amount,
                    acct,
                    f"company-901:{vendor}",
                )
            )
    insert_txns(conn, rows)
    template, company = TEMPLATES["anom.vendor_outliers"], load_company(conn, "company-901")
    period = {"start_date": "2026-03-01", "end_date": "2026-03-31"}
    # median 25: 75 is not more than 3 x 25 (a lower median of 20 would wrongly flag it), 76 is
    assert template.answer(conn, company, {"vendor_id": "company-901:vend-a", **period}).expected.id_set == []
    assert template.answer(conn, company, {"vendor_id": "company-901:vend-b", **period}).expected.id_set == [
        "company-901:vend-b-3:1"
    ]


def test_degenerate_rule_for_id_sets():
    assert id_set_answer([]).degenerate
    assert not id_set_answer(["a"]).degenerate
    assert not id_set_answer([str(i) for i in range(MAX_IDS)]).degenerate
    assert id_set_answer([str(i) for i in range(MAX_IDS + 1)]).degenerate


def test_mom_change_is_degenerate_when_either_month_is_empty(conn, ledger):
    template, company = TEMPLATES["agg.mom_change_account"], load_company(conn, "company-901")
    assert template.answer(conn, company, {"account_id": "company-901:acct-insurance", "month": "2026-05"}).degenerate
    assert not template.answer(conn, company, {"account_id": "company-901:acct-rent", "month": "2026-05"}).degenerate


INVALID = [
    ("agg.total_by_account_period", {"account_id": "company-902:acct-rent"}, "is not an account of company-901"),
    ("agg.total_by_account_period", {"start_date": "2026-02-30"}, "is not a valid date"),
    ("agg.total_by_account_period", {"start_date": "2026-04-01", "end_date": "2026-03-01"}, "is after end_date"),
    (
        "agg.total_by_account_period",
        {"start_date": "2027-01-01", "end_date": "2027-02-01"},
        "outside company-901's data",
    ),
    ("agg.total_by_vendor_period", {"vendor_id": "company-901:vend-nope"}, "is not a vendor of company-901"),
    ("agg.mom_change_account", {"month": "2026-01"}, "needs data for it and the month before"),
    ("agg.mom_change_account", {"month": "2026-13"}, "is not a valid month"),
    ("agg.share_of_expense", {"account_id": "company-901:acct-inventory"}, "has account_type asset, expected expense"),
    ("book.accrual", {"liability_account_id": "company-901:acct-rent"}, "expected liability"),
    ("book.prepaid_amortization", {"total": "0.01", "months": 2}, "too small to split over 2 months"),
    ("book.reclassify", {"txn_id": "company-902:bill-00001:1"}, "is not a transaction of company-901"),
    ("book.reclassify", {"to_account_id": "company-901:acct-rent"}, "is already in Rent and Lease"),
]
PERIOD = {"start_date": "2026-01-01", "end_date": "2026-03-31"}
VALID = {
    "agg.total_by_account_period": {"account_id": "company-901:acct-rent", **PERIOD},
    "agg.total_by_vendor_period": {"vendor_id": "company-901:vend-insurer", **PERIOD},
    "agg.mom_change_account": {"account_id": "company-901:acct-rent", "month": "2026-03"},
    "agg.share_of_expense": {"account_id": "company-901:acct-rent", **PERIOD},
    "book.accrual": {
        "expense_account_id": "company-901:acct-utilities",
        "liability_account_id": "company-901:acct-accounts-payable",
        "amount": "500.00",
        "entry_date": "2026-03-31",
    },
    "book.prepaid_amortization": {
        "prepaid_account_id": "company-901:acct-prepaid-expenses",
        "expense_account_id": "company-901:acct-insurance",
        "total": "1000.00",
        "months": 3,
        "start_month": "2026-01",
    },
    "book.reclassify": {"txn_id": "company-901:bill-00001:1", "to_account_id": "company-901:acct-utilities"},
}


@pytest.mark.parametrize(("template_id", "change", "message"), INVALID)
def test_validate_rejects_bad_params_with_clear_errors(conn, ledger, template_id, change, message):
    template, company = TEMPLATES[template_id], load_company(conn, "company-901")
    assert template.validate(conn, company, VALID[template_id])
    with pytest.raises(ParamError, match=message):
        template.validate(conn, company, VALID[template_id] | change)


def test_reclassify_rejects_non_positive_transactions(conn, ledger):
    negative = next(t for t in ledger.company_txns("company-901") if t.amount < 0)  # the -262.50 wage credit
    template, company = TEMPLATES["book.reclassify"], load_company(conn, "company-901")
    with pytest.raises(ParamError, match="only positive amounts can be reclassified"):
        template.validate(conn, company, VALID["book.reclassify"] | {"txn_id": negative.txn_id})


def test_validate_normalizes_money_and_reports_schema_errors(conn, ledger):
    template, company = TEMPLATES["book.accrual"], load_company(conn, "company-901")
    assert template.validate(conn, company, VALID["book.accrual"] | {"amount": 500})["amount"] == "500.00"
    with pytest.raises(ParamError, match="amount: Decimal input should have no more than 2 decimal places"):
        template.validate(conn, company, VALID["book.accrual"] | {"amount": "1.005"})
    with pytest.raises(ParamError, match="params: Input should be a valid dictionary"):
        template.validate(conn, company, ["not", "a", "dict"])


# ---------- samplers ----------


@pytest.mark.parametrize("template_id", list(TEMPLATES))
def test_sampler_is_deterministic_and_stays_inside_the_company(conn, ledger, template_id):
    template = TEMPLATES[template_id]
    for cid in COMPANIES:
        company = load_company(conn, cid)
        txn_ids = {t.txn_id for t in ledger.company_txns(cid)}
        for difficulty in template.difficulties:
            for seed in range(5):
                p = template.sample(conn, company, random.Random(seed), difficulty)
                assert p == template.sample(conn, company, random.Random(seed), difficulty)
                assert p is not None
                for key, value in p.items():
                    if key.endswith("account_id"):
                        assert value in company.accounts
                    elif key == "vendor_id":
                        assert value in company.vendors
                    elif key == "txn_id":
                        assert value in txn_ids
                    elif key.endswith("_date"):
                        assert company.first <= _d(value) <= company.last
                    elif key in ("month", "start_month"):
                        assert company.first <= _d(value + "-01")
                if template.family == TaskFamily.aggregation and "account_id" in p:
                    assert company.accounts[p["account_id"]][1] == "expense"


@pytest.mark.parametrize("template", PERIOD_AGGREGATIONS, ids=lambda t: t.template_id)
def test_aggregation_period_length_follows_difficulty(conn, ledger, template):
    company = load_company(conn, "company-901")
    for difficulty in template.difficulties:
        lo, hi = TIER_DAYS[difficulty]
        for seed in range(20):
            p = template.sample(conn, company, random.Random(seed), difficulty)
            start, end = _d(p["start_date"]), _d(p["end_date"])
            assert lo <= (end - start).days + 1 <= hi
            if difficulty == 1:
                assert (start.year, start.month) == (end.year, end.month)


def test_mom_sampler_never_picks_the_first_month(conn, ledger):
    template, company = TEMPLATES["agg.mom_change_account"], load_company(conn, "company-901")
    months = {template.sample(conn, company, random.Random(s), 2)["month"] for s in range(60)}
    # data runs 2026-01-01..2026-06-29: the month and the one before must both be full months
    assert months == {"2026-02", "2026-03", "2026-04", "2026-05"}


def test_bookkeeping_samplers_follow_the_dataset_decisions(conn, ledger):
    for cid in TRAIN:
        company = load_company(conn, cid)
        rows = {t.txn_id: t for t in ledger.company_txns(cid)}
        accruals = set()
        for seed in range(40):
            p = TEMPLATES["book.accrual"].sample(conn, company, random.Random(seed), 1)
            accruals.add(
                (company.account_name(p["expense_account_id"]), company.account_name(p["liability_account_id"]))
            )
        assert accruals == {
            ("Wages", "Wages Payable"),
            *((e, "Accounts Payable") for e in ("Rent and Lease", "Utilities", "Freight and Delivery", "Insurance")),
        }
        prepaid_targets, months, reclass_targets = set(), set(), set()
        for seed in range(40):
            prepaid = TEMPLATES["book.prepaid_amortization"].sample(conn, company, random.Random(seed), 2)
            assert prepaid["prepaid_account_id"] == f"{cid}:acct-prepaid-expenses"
            prepaid_targets.add(company.account_name(prepaid["expense_account_id"]))
            months.add(prepaid["months"])
            reclass = TEMPLATES["book.reclassify"].sample(conn, company, random.Random(seed), 3)
            txn = rows[reclass["txn_id"]]
            assert txn.txn_type == "bill" and txn.amount > 0
            assert reclass["to_account_id"] != txn.account_id
            assert company.accounts[reclass["to_account_id"]][1] == "expense"
            reclass_targets.add(company.account_name(reclass["to_account_id"]))
        # only expenses a business prepays; at most 6 monthly entries so the task fits MAX_STEPS
        assert prepaid_targets == {"Rent and Lease", "Insurance"}
        assert months == {2, 3, 4}  # 6 needs six month-ends; the synthetic ledger has five
        # never COGS, wages or other accounts a vendor bill is not moved into
        assert reclass_targets == {"Rent and Lease", "Utilities", "Insurance"}


def test_anomaly_sampler_skips_a_company_without_matches(conn, ledger):
    conn.execute("DELETE FROM ledger.transactions WHERE company_id = 'company-902' AND amount = 2950.37")
    conn.execute("DELETE FROM ledger.transactions WHERE txn_id = 'company-902:planted-dup-0001:1'")
    for template_id, difficulty in (("anom.vendor_outliers", 3), ("anom.duplicate_payments", 2)):
        template = TEMPLATES[template_id]
        assert template.sample(conn, load_company(conn, "company-902"), random.Random(0), difficulty) is None
        task = sample_task(
            conn, template_id, "company-902", rng=random.Random(0), difficulty=difficulty, origin=Origin.generated
        )
        assert task is None
        assert sample_task(
            conn, template_id, "company-901", rng=random.Random(0), difficulty=difficulty, origin=Origin.generated
        )


def test_sample_task_resamples_degenerate_answers_then_gives_up(conn, ledger, monkeypatch):
    template = get_template("agg.total_by_account_period")
    empty = {"account_id": "company-901:acct-rent", "start_date": "2026-01-02", "end_date": "2026-01-08"}  # no rent
    good = {"account_id": "company-901:acct-rent", "start_date": "2026-01-01", "end_date": "2026-01-08"}

    def scripted(results):
        calls = []

        def sample(conn, company, rng, difficulty):
            calls.append(1)
            return results[min(len(calls), len(results)) - 1]

        return sample, calls

    def run():
        return sample_task(
            conn, template.template_id, "company-901", rng=random.Random(0), difficulty=1, origin=Origin.generated
        )

    sample, calls = scripted([empty, empty, good])
    monkeypatch.setattr(template, "sample", sample)
    task = run()
    assert task.params == good and task.expected.number == Decimal("2500.00") and len(calls) == 3

    sample, calls = scripted([empty])
    monkeypatch.setattr(template, "sample", sample)
    assert run() is None and len(calls) == MAX_TRIES


def test_sample_task_rejects_answers_with_more_than_50_ids(conn, ledger, monkeypatch):
    acct = "company-902:acct-utilities"
    insert_txns(
        conn,
        [(f"company-902:x-round-{i}:1", "company-902", date(2026, 2, 2), "100.00", acct, None) for i in range(MAX_IDS)],
    )
    template = get_template("anom.round_amounts_over")
    every_round = {"threshold": "100.00", "start_date": "2026-01-01", "end_date": "2026-06-29"}
    assert template.answer(conn, load_company(conn, "company-902"), every_round).degenerate
    monkeypatch.setattr(template, "sample", lambda *a: every_round)
    assert (
        sample_task(
            conn, template.template_id, "company-902", rng=random.Random(0), difficulty=1, origin=Origin.generated
        )
        is None
    )


@pytest.mark.parametrize("template_id", list(TEMPLATES))
def test_every_template_yields_50_valid_non_degenerate_tasks_on_train_companies(conn, ledger, template_id):
    template = TEMPLATES[template_id]
    for i in range(50):
        cid, difficulty = TRAIN[i % 2], template.difficulties[i % len(template.difficulties)]
        task = sample_task(conn, template_id, cid, rng=random.Random(i), difficulty=difficulty, origin=Origin.generated)
        assert task is not None, (cid, difficulty, i)
        company = load_company(conn, cid)
        assert template.validate(conn, company, task.params) == task.params
        answer = template.answer(conn, company, task.params)
        assert not answer.degenerate and answer.expected == task.expected
        assert (task.company_id, task.split.value, task.difficulty, task.family) == (
            cid,
            "train",
            difficulty,
            template.family,
        )
        assert task.prompt in [template.render(company, task.params, ph) for ph in template.phrasings]


# ---------- phrasings ----------


@pytest.mark.parametrize("template_id", list(TEMPLATES))
def test_phrasings_render_fully_and_show_names_not_ids(conn, ledger, template_id):
    template = TEMPLATES[template_id]
    for cid in TRAIN:
        company = load_company(conn, cid)
        p = template.validate(conn, company, template.sample(conn, company, random.Random(1), template.difficulty))
        values = template.placeholders(company, p)
        for phrasing in template.phrasings:
            assert placeholder_names(phrasing) == set(values), phrasing  # every phrasing states every param
            text = template.render(company, p, phrasing)
            assert "{" not in text and "}" not in text
            if template_id == "book.reclassify":  # the documented exception: the txn_id identifies the transaction
                assert p["txn_id"] in text
                text = text.replace(p["txn_id"], "")
            assert cid not in text, text
        for key in ("account", "expense_account", "liability_account", "prepaid_account", "to_account"):
            if key in values:
                assert values[key] in {name for name, _ in company.accounts.values()}
        if "vendor" in values:
            assert values["vendor"] in company.vendors.values()
