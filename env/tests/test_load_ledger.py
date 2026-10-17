"""Loader (P1-02) and planted duplicates (P1-03).

Unit tests use the hand-made raw-format fixture in tests/data/raw_mini (three companies, real YAML field names).
The real-dataset test runs only with SCAR_REAL_DATA_TESTS=1 and the extracted data at SCAR_REAL_DATA_DIR.
"""

import json
import os
import random
import shutil
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
import yaml

from scar_env.load_ledger import (
    CHANGES_BEGIN,
    CHANGES_END,
    Txn,
    assign_splits,
    company_dir,
    load,
    main,
    plant_duplicates,
    read_company,
    read_index,
    read_yaml,
    table_checksums,
    update_changes_doc,
)

MINI = Path(__file__).parent / "data" / "raw_mini"
REAL = Path(
    os.environ.get("SCAR_REAL_DATA_DIR", "/Users/doyoonkim/scar-data/quickbooks-hackathon-2026/extracted/hackathon-100")
)

# docs/DATASET.md, "Split (Section 9.1)"
REAL_EVAL = {
    f"company-{n:03d}"
    for n in (
        1,
        2,
        3,
        11,
        15,
        17,
        22,
        23,
        25,
        26,
        27,
        33,
        34,
        39,
        45,
        48,
        49,
        50,
        52,
        62,
        74,
        77,
        79,
        80,
        82,
        83,
        85,
        94,
        95,
        100,
    )
}

# SPEC 9.5 anom.duplicate_payments: every pair with the same vendor_id and amount and dates <= 3 days apart,
# as (earlier txn_id, later txn_id); same-day pairs are ordered by txn_id.
DUPLICATE_PAIRS_SQL = """
SELECT a.txn_id, b.txn_id
FROM ledger.transactions a
JOIN ledger.transactions b
  ON b.vendor_id = a.vendor_id AND b.amount = a.amount
 AND (b.txn_date, b.txn_id) > (a.txn_date, a.txn_id) AND b.txn_date - a.txn_date <= 3
"""


def duplicate_pairs(conn) -> set[tuple[str, str]]:
    return set(conn.execute(DUPLICATE_PAIRS_SQL).fetchall())


def planted_pairs(conn) -> set[tuple[str, str]]:
    return {tuple(ids) for (ids,) in conn.execute("SELECT txn_ids FROM ledger.planted_anomalies")}


def pnl_expense_totals(data_dir: Path) -> dict[str, Decimal]:
    """Sum of the non-Income lines of each company's reports/profit_and_loss.yaml full_period."""
    totals = {}
    for entry in read_index(data_dir):
        report = read_yaml(company_dir(data_dir, entry) / "reports" / "profit_and_loss.yaml")
        lines = report["full_period"]["lines"]
        totals[entry["company_id"]] = sum((Decimal(x["amount"]) for x in lines if x["section"] != "Income"), Decimal(0))
    return totals


def ledger_expense_totals(conn) -> dict[str, Decimal]:
    """SUM(amount) on expense accounts per company, planted rows excluded."""
    return dict(
        conn.execute(
            "SELECT t.company_id, sum(t.amount) FROM ledger.transactions t"
            " JOIN ledger.accounts a ON a.account_id = t.account_id"
            " WHERE a.account_type = 'expense'"
            " AND t.txn_id NOT IN (SELECT txn_ids ->> 1 FROM ledger.planted_anomalies)"
            " GROUP BY t.company_id"
        ).fetchall()
    )


def integrity_violations(conn) -> dict[str, int]:
    """Orphans and cross-company references, checked with joins rather than trusting the constraints."""
    checks = {
        "account_company": "SELECT count(*) FROM ledger.accounts a"
        " LEFT JOIN ledger.companies c ON c.company_id = a.company_id WHERE c.company_id IS NULL",
        "vendor_company": "SELECT count(*) FROM ledger.vendors v"
        " LEFT JOIN ledger.companies c ON c.company_id = v.company_id WHERE c.company_id IS NULL",
        "txn_account": "SELECT count(*) FROM ledger.transactions t LEFT JOIN ledger.accounts a"
        " ON a.account_id = t.account_id AND a.company_id = t.company_id WHERE a.account_id IS NULL",
        "txn_vendor": "SELECT count(*) FROM ledger.transactions t LEFT JOIN ledger.vendors v"
        " ON v.vendor_id = t.vendor_id AND v.company_id = t.company_id"
        " WHERE t.vendor_id IS NOT NULL AND v.vendor_id IS NULL",
        "planted_txn": "SELECT count(*) FROM ledger.planted_anomalies p"
        " CROSS JOIN jsonb_array_elements_text(p.txn_ids) AS ids(txn_id) LEFT JOIN ledger.transactions t"
        " ON t.txn_id = ids.txn_id AND t.company_id = p.company_id WHERE t.txn_id IS NULL",
    }
    return {name: conn.execute(sql).fetchone()[0] for name, sql in checks.items()}


def mini_company(cid: str):
    entry = next(e for e in read_index(MINI) if e["company_id"] == cid)
    return read_company(company_dir(MINI, entry), cid)


# ---------- splits ----------


def test_split_reproduces_dataset_md_on_real_company_ids():
    splits = assign_splits([f"company-{n:03d}" for n in range(1, 101)])
    assert {cid for cid, s in splits.items() if s == "eval"} == REAL_EVAL
    assert sum(s == "train" for s in splits.values()) == 70


def test_split_rules():
    ids = [f"company-{n:03d}" for n in range(1, 11)]
    assert assign_splits(ids) == assign_splits(list(reversed(ids)))
    assert sum(s == "eval" for s in assign_splits(ids).values()) == 3
    assert sum(s == "eval" for s in assign_splits(ids[:3]).values()) == 2  # minimum of 2 eval companies


# ---------- mapping (docs/DATASET.md) ----------


def test_accounts_mapping_and_prepaid_account():
    accounts, _, _ = mini_company("company-801")
    types = {aid.split(":", 1)[1]: (cid, typ) for aid, cid, _, typ in accounts}
    assert types["acct-checking"] == ("company-801", "asset")
    assert types["acct-payable"] == ("company-801", "liability")
    assert types["acct-opening-equity"] == ("company-801", "equity")
    assert types["acct-product-income"] == ("company-801", "income")  # raw classification "revenue"
    assert types["acct-cogs"] == ("company-801", "expense")  # raw account_type cost_of_goods_sold
    assert types["acct-interest-expense"] == ("company-801", "expense")  # raw account_type other_expense
    assert ("company-801:acct-prepaid-expenses", "company-801", "Prepaid Expenses", "asset") in accounts
    assert len(accounts) == 13 + 1


def test_vendors_mapping():
    _, vendors, _ = mini_company("company-802")
    assert vendors == [
        ("company-802:vend-subcontractor-1", "company-802", "Brightfield Tile & Stone"),
        ("company-802:vend-electricity", "company-802", "Copperfield Water District"),
    ]


def test_bill_rows():
    _, _, txns = mini_company("company-801")
    by_id = {t.txn_id: t for t in txns}
    # product bill: item line -> the item's purchase account, item-line description
    assert by_id["company-801:bill-00001:1"] == Txn(
        "company-801:bill-00001:1",
        "company-801",
        date(2026, 1, 5),
        Decimal("615.00"),
        "company-801:acct-inventory",
        "company-801:vend-supplier",
        "Case of fifty lint-free shop towels.",
        "bill",
    )
    # overhead bill: account line; description is the line description, never the bill memo
    rent = by_id["company-801:bill-operating-00001:1"]
    assert (rent.account_id, rent.vendor_id, rent.amount) == (
        "company-801:acct-rent",
        "company-801:vend-landlord",
        Decimal("1500.00"),
    )
    assert rent.description == "Premises rent for the month (premises)."
    assert sum(t.txn_type == "bill" for t in txns) == 6


def test_journal_rows_sign_rule_and_skipped_lines():
    _, _, txns = mini_company("company-801")
    je = {t.txn_id.removeprefix("company-801:"): t for t in txns if t.txn_type != "bill"}
    assert sorted(je) == [
        "je-cogs-00001:1",
        "je-interest-00001:1",
        "je-labor-00001:2",
        "je-labor-00002:1",
        "je-labor-00002:2",
        "je-payroll-00001:1",
    ]  # opening_equity and wage_payment have no expense line; balance-sheet sides are skipped
    assert je["je-cogs-00001:1"].amount == Decimal("166.05")  # debit to an expense account
    assert je["je-labor-00001:2"].amount == Decimal("-262.50")  # credit to an expense account
    assert (je["je-labor-00002:1"].amount, je["je-labor-00002:2"].amount) == (Decimal("262.50"), Decimal("-262.50"))
    wages = je["je-payroll-00001:1"]
    assert (wages.account_id, wages.vendor_id, wages.txn_type, wages.description, wages.txn_date) == (
        "company-801:acct-manager-wages",
        None,
        "wage_accrual",
        "Biweekly wages earned by Ada Holloway.",
        date(2026, 1, 16),
    )


def test_company_dir_rejects_unexpected_ids():
    with pytest.raises(ValueError):
        company_dir(MINI, {"company_id": "../company-801", "dataset_id": "world-0000000000000801"})
    with pytest.raises(ValueError):
        company_dir(MINI, {"company_id": "company-801", "dataset_id": "world-../../x"})


def test_requires_libyaml(monkeypatch):
    monkeypatch.delattr(yaml, "CSafeLoader")
    with pytest.raises(RuntimeError, match="libyaml"):
        read_yaml(MINI / "company-801" / "rwm-v4" / "world-0000000000000801" / "vendors.yaml")


# ---------- planting rules (SPEC 9.5) ----------


def _bill(cid: str, doc: str, d: date, amount: str) -> Txn:
    return Txn(
        f"{cid}:{doc}:1",
        cid,
        d,
        Decimal(amount),
        f"{cid}:acct-freight",
        f"{cid}:vend-freight-vendor",
        "Outbound carriage.",
        "bill",
    )


def _je(cid: str, d: date) -> Txn:
    return Txn(f"{cid}:je-cogs-00001:1", cid, d, Decimal("5.00"), f"{cid}:acct-cogs", None, "COGS", "cost_of_goods")


def test_plant_rules():
    shifts = set()
    for n in range(1, 101):
        cid = f"company-{n:03d}"
        last = date(2026, 10, 6)
        bills = [
            _bill(cid, f"bill-{i:05d}", date(2025, 10, 6) + timedelta(days=9 * i), f"{100 + i}.25") for i in range(40)
        ]
        late = [_bill(cid, f"bill-late-{i}", last - timedelta(days=i), "99.00") for i in range(3)]  # never copied
        txns = bills + late + [_je(cid, last)]
        copies, anomalies = plant_duplicates(cid, txns)
        originals = {t.txn_id: t for t in txns}

        assert 5 <= len(copies) <= 15 and len(anomalies) == len(copies)
        assert len({a.txn_ids[0] for a in anomalies}) == len(anomalies)  # distinct originals
        for k, (copy, anomaly) in enumerate(zip(copies, anomalies, strict=True), start=1):
            original = originals[anomaly.txn_ids[0]]
            days = anomaly.details["days_shifted"]
            assert anomaly.anomaly_id == f"{cid}:planted-dup-{k:04d}" and copy.txn_id == f"{anomaly.anomaly_id}:1"
            assert anomaly.company_id == cid and anomaly.kind == "duplicate_payment"
            assert anomaly.txn_ids == [original.txn_id, copy.txn_id]
            assert anomaly.details == {"original_txn_id": original.txn_id, "days_shifted": days}
            assert original.txn_type == "bill" and original not in late
            assert 1 <= days <= 3 and copy.txn_date == original.txn_date + timedelta(days=days) <= last
            assert copy._replace(txn_id=original.txn_id, txn_date=original.txn_date) == original
            shifts.add(days)

        shuffled = txns[:]
        random.Random(n).shuffle(shuffled)
        assert plant_duplicates(cid, shuffled) == (copies, anomalies)  # deterministic, input order irrelevant
    assert shifts == {1, 2, 3}


def test_plant_copies_every_eligible_bill_when_too_few():
    cid = "company-001"
    txns = [_bill(cid, "bill-00001", date(2026, 1, 1), "10.00"), _bill(cid, "bill-00002", date(2026, 1, 11), "20.00")]
    copies, _ = plant_duplicates(cid, [*txns, _je(cid, date(2026, 2, 1))])
    assert len(copies) == 2


# ---------- full load into the database ----------


def test_load_fixture(conn, db_url):
    summary = load(MINI, db_url)
    rows = summary["rows"]
    planted = dict(conn.execute("SELECT company_id, count(*) FROM ledger.planted_anomalies GROUP BY 1"))
    assert planted["company-801"] in (5, 6) and planted["company-802"] == 4 and planted["company-803"] == 2
    assert rows == {
        "companies": 3,
        "accounts": 14 + 10 + 10,
        "vendors": 3 + 2 + 1,
        "transactions": 12 + 6 + 4 + sum(planted.values()),
        "planted_anomalies": sum(planted.values()),
    }
    assert summary["splits"] == {"eval": 2, "train": 1}
    assert summary["checksums"] == table_checksums(conn)
    companies = conn.execute("SELECT * FROM ledger.companies ORDER BY company_id").fetchall()
    assert companies[0] == ("company-801", "Millbrook Supply Co", "Industrial Supplies Merchant Wholesalers", "eval")
    assert conn.execute(
        "SELECT count(*) FROM ledger.accounts WHERE account_id LIKE '%:acct-prepaid-expenses'"
        " AND name = 'Prepaid Expenses' AND account_type = 'asset'"
    ).fetchone() == (3,)
    violations = integrity_violations(conn)
    assert violations == dict.fromkeys(violations, 0)
    assert ledger_expense_totals(conn) == pnl_expense_totals(MINI)


def test_duplicate_sql_finds_every_planted_pair(conn, db_url):
    load(MINI, db_url)
    planted = planted_pairs(conn)
    assert len(planted) == conn.execute("SELECT count(*) FROM ledger.planted_anomalies").fetchone()[0]
    assert planted <= duplicate_pairs(conn)


def test_reload_replaces_data_and_is_idempotent(conn, db_url, ledger):
    first = load(MINI, db_url)
    assert [r[0] for r in conn.execute("SELECT company_id FROM ledger.companies ORDER BY 1")] == [
        "company-801",
        "company-802",
        "company-803",
    ]  # the synthetic ledger loaded by the `ledger` fixture is gone
    assert load(MINI, db_url) == first


def test_checksums_detect_a_changed_row(conn, db_url):
    before = load(MINI, db_url)["checksums"]
    conn.execute("UPDATE ledger.transactions SET amount = amount + 0.01 WHERE txn_id = 'company-801:bill-00001:1'")
    after = table_checksums(conn)
    assert after["transactions"] != before["transactions"]
    assert {k: v for k, v in after.items() if k != "transactions"} == {
        k: v for k, v in before.items() if k != "transactions"
    }


def test_failed_load_leaves_previous_data(conn, db_url, tmp_path):
    before = load(MINI, db_url)
    broken = tmp_path / "raw"
    shutil.copytree(MINI, broken)
    bills = broken / "company-803" / "rwm-v4" / "world-0000000000000803" / "bills.yaml"
    bills.write_text(bills.read_text().replace("vendor_id: vend-insurer", "vendor_id: vend-missing", 1))
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        load(broken, db_url)
    assert table_checksums(conn) == before["checksums"]


def test_cli_prints_summary_and_regenerates_changes_doc(conn, db_url, tmp_path, capsys):
    doc = tmp_path / "DATASET_CHANGES.md"
    doc.write_text(f"# Changes\n\n{CHANGES_BEGIN}\nstale\n{CHANGES_END}\n\nAfter.\n")
    argv = ["--data-dir", str(MINI), "--database-url", db_url, "--changes-doc", str(doc)]
    main(argv)
    summary = json.loads(capsys.readouterr().out)
    assert summary["rows"]["companies"] == 3 and summary["splits"] == {"eval": 2, "train": 1}
    assert summary["checksums"] == table_checksums(conn)
    text = doc.read_text()
    assert text.startswith("# Changes\n\n") and text.endswith(f"{CHANGES_END}\n\nAfter.\n") and "stale" not in text
    for original, copy in planted_pairs(conn):
        assert f"| `{copy}` | `{original}` | +" in text
    assert "| company-802 | 4 |" in text
    main(argv)
    assert doc.read_text() == text


def test_update_changes_doc_requires_markers(tmp_path):
    doc = tmp_path / "doc.md"
    doc.write_text("no markers\n")
    with pytest.raises(ValueError):
        update_changes_doc(doc, "x")


# ---------- real dataset (opt-in) ----------


@pytest.mark.skipif(
    os.environ.get("SCAR_REAL_DATA_TESTS") != "1" or not REAL.is_dir(),
    reason="set SCAR_REAL_DATA_TESTS=1 and put the extracted dataset at SCAR_REAL_DATA_DIR",
)
def test_real_dataset(conn, db_url):
    first = load(REAL, db_url)
    planted = first["rows"]["planted_anomalies"]
    assert first["rows"] == {
        "companies": 100,
        "accounts": 3_700 + 100,
        "vendors": 821,
        "transactions": 159_790 + planted,
        "planted_anomalies": planted,
    }
    per_company = dict(conn.execute("SELECT company_id, count(*) FROM ledger.planted_anomalies GROUP BY 1"))
    assert len(per_company) == 100 and all(5 <= n <= 15 for n in per_company.values())
    assert first["splits"] == {"eval": 30, "train": 70}
    assert {cid for (cid,) in conn.execute("SELECT company_id FROM ledger.companies WHERE split = 'eval'")} == REAL_EVAL
    assert ledger_expense_totals(conn) == pnl_expense_totals(REAL)
    violations = integrity_violations(conn)
    assert violations == dict.fromkeys(violations, 0)
    assert planted_pairs(conn) <= duplicate_pairs(conn)
    assert load(REAL, db_url) == first
