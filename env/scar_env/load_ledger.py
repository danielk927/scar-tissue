"""Load the raw QuickBooks hackathon dataset into the canonical ledger (SPEC 8.7, P1-02 and P1-03).

Mapping, sign rule and IDs follow docs/DATASET.md exactly; splits follow SPEC 9.1; planted duplicates SPEC 9.5.
Every modification of the raw data is disclosed in docs/DATASET_CHANGES.md.

    uv run python -m scar_env.load_ledger --data-dir <hackathon-100 dir> [--database-url URL] [--changes-doc PATH]
"""

import argparse
import json
import random
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import NamedTuple

import psycopg
import yaml
from psycopg.types.json import Jsonb
from scar_schemas.constants import EVAL_COMPANY_FRACTION, SPLIT_SEED

from scar_env.config import get_settings

# raw accounts.yaml `classification` -> canonical account_type (docs/DATASET.md)
ACCOUNT_TYPES = {
    "asset": "asset",
    "liability": "liability",
    "equity": "equity",
    "revenue": "income",
    "expense": "expense",
}
PREPAID_ACCOUNT = ("acct-prepaid-expenses", "Prepaid Expenses", "asset")  # added to every company (DATASET_CHANGES)

PLANT_MIN, PLANT_MAX, PLANT_MAX_SHIFT_DAYS = 5, 15, 3

# ledger tables in foreign-key order, with their primary keys
TABLES = {
    "companies": "company_id",
    "accounts": "account_id",
    "vendors": "vendor_id",
    "transactions": "txn_id",
    "planted_anomalies": "anomaly_id",
}
COLUMNS = {
    "companies": "company_id, name, industry, split",
    "accounts": "account_id, company_id, name, account_type",
    "vendors": "vendor_id, company_id, name",
    "transactions": "txn_id, company_id, txn_date, amount, account_id, vendor_id, description, txn_type",
    "planted_anomalies": "anomaly_id, company_id, kind, txn_ids, details",
}

CHANGES_BEGIN = "<!-- BEGIN GENERATED: python -m scar_env.load_ledger --changes-doc; do not edit by hand -->"
CHANGES_END = "<!-- END GENERATED -->"


class Txn(NamedTuple):
    """One ledger.transactions row, fields in table column order."""

    txn_id: str
    company_id: str
    txn_date: date
    amount: Decimal
    account_id: str
    vendor_id: str | None
    description: str | None
    txn_type: str


class Anomaly(NamedTuple):
    """One ledger.planted_anomalies row, fields in table column order."""

    anomaly_id: str
    company_id: str
    kind: str
    txn_ids: list[str]
    details: dict


@dataclass
class Ledger:
    companies: list[tuple[str, str, str | None, str]] = field(default_factory=list)
    accounts: list[tuple[str, str, str, str]] = field(default_factory=list)
    vendors: list[tuple[str, str, str]] = field(default_factory=list)
    transactions: list[Txn] = field(default_factory=list)
    planted_anomalies: list[Anomaly] = field(default_factory=list)


def assign_splits(company_ids: list[str]) -> dict[str, str]:
    """SPEC 9.1: sort, shuffle with SPLIT_SEED; the first max(2, round(fraction * n)) are eval, the rest train."""
    ids = sorted(company_ids)
    random.Random(SPLIT_SEED).shuffle(ids)
    n_eval = max(2, round(EVAL_COMPANY_FRACTION * len(ids)))
    return {cid: "eval" if i < n_eval else "train" for i, cid in enumerate(ids)}


def read_index(data_dir: Path) -> list[dict]:
    """index.json entries sorted by company_id."""
    return sorted(json.loads((data_dir / "index.json").read_text()), key=lambda e: e["company_id"])


def company_dir(data_dir: Path, entry: dict) -> Path:
    """`{company_id}/rwm-v4/{dataset_id}`; the IDs are validated first because the data is untrusted."""
    cid, dataset_id = entry["company_id"], entry["dataset_id"]
    if not re.fullmatch(r"company-\d{3}", cid) or not re.fullmatch(r"world-[0-9a-f]{16}", dataset_id):
        raise ValueError(f"unexpected index.json ids: {cid!r}, {dataset_id!r}")
    return data_dir / cid / "rwm-v4" / dataset_id


def read_yaml(path: Path):
    loader = getattr(yaml, "CSafeLoader", None)
    if loader is None:
        raise RuntimeError("PyYAML has no libyaml binding (yaml.CSafeLoader); install a pyyaml wheel built with it")
    with path.open("rb") as f:
        return yaml.load(f, Loader=loader)


def _money(value: object) -> Decimal:
    """Raw amounts are two-decimal strings; anything else (a float) could silently lose cents."""
    if not isinstance(value, str):
        raise ValueError(f"expected a money string, got {value!r}")
    return Decimal(value)


def read_company(world: Path, cid: str) -> tuple[list, list, list[Txn]]:
    """Accounts, vendors and transactions of one company, mapped per docs/DATASET.md."""
    raw_accounts = read_yaml(world / "accounts.yaml")
    classification = {a["id"]: a["classification"] for a in raw_accounts}
    accounts = [(f"{cid}:{a['id']}", cid, a["name"], ACCOUNT_TYPES[a["classification"]]) for a in raw_accounts]
    raw_id, name, account_type = PREPAID_ACCOUNT
    accounts.append((f"{cid}:{raw_id}", cid, name, account_type))
    vendors = [(f"{cid}:{v['id']}", cid, v["display_name"]) for v in read_yaml(world / "vendors.yaml")]
    purchase_account = {i["id"]: i["purchase_account_id"] for i in read_yaml(world / "items.yaml")}

    txns = []
    for bill in read_yaml(world / "bills.yaml"):
        lines = [(line, line["account_id"]) for line in bill["account_lines"]]
        lines += [(line, purchase_account[line["item_id"]]) for line in bill["item_lines"]]
        for line, account in lines:
            txns.append(
                Txn(
                    f"{cid}:{bill['id']}:{line['line_num']}",
                    cid,
                    date.fromisoformat(bill["txn_date"]),
                    _money(line["amount"]),
                    f"{cid}:{account}",
                    f"{cid}:{bill['vendor_id']}",
                    line["description"],
                    "bill",
                )
            )
    for je in read_yaml(world / "journal_entries.yaml"):
        for line in je["lines"]:
            if classification[line["account_id"]] != "expense":
                continue
            txns.append(
                Txn(
                    f"{cid}:{je['id']}:{line['line_num']}",
                    cid,
                    date.fromisoformat(je["txn_date"]),
                    _money(line["debit_amount"]) - _money(line["credit_amount"]),
                    f"{cid}:{line['account_id']}",
                    None,
                    je["memo"],
                    je["purpose"],
                )
            )
    return accounts, vendors, txns


def plant_duplicates(cid: str, txns: list[Txn]) -> tuple[list[Txn], list[Anomaly]]:
    """SPEC 9.5: copy 5-15 real bill rows (only bills have vendors) with a new txn_id, dated 1-3 days later.

    Originals are bills dated at least 3 days before the company's last transaction, so a copy never lands past it.
    If a company has fewer such bills than the drawn count, every one of them is copied (never in the real data).
    """
    last_date = max(t.txn_date for t in txns)
    max_shift = timedelta(days=PLANT_MAX_SHIFT_DAYS)
    eligible = sorted(
        (t for t in txns if t.txn_type == "bill" and t.txn_date + max_shift <= last_date), key=lambda t: t.txn_id
    )
    rng = random.Random(f"plant:{cid}")
    count = rng.randint(PLANT_MIN, PLANT_MAX)
    originals = sorted(rng.sample(eligible, min(count, len(eligible))), key=lambda t: t.txn_id)
    copies, anomalies = [], []
    for n, original in enumerate(originals, start=1):
        days = rng.randint(1, PLANT_MAX_SHIFT_DAYS)
        anomaly_id = f"{cid}:planted-dup-{n:04d}"
        copy = original._replace(txn_id=f"{anomaly_id}:1", txn_date=original.txn_date + timedelta(days=days))
        copies.append(copy)
        details = {"original_txn_id": original.txn_id, "days_shifted": days}
        anomalies.append(Anomaly(anomaly_id, cid, "duplicate_payment", [original.txn_id, copy.txn_id], details))
    return copies, anomalies


def read_ledger(data_dir: Path) -> Ledger:
    """Parse the raw files (only the five YAML files the mapping needs) into canonical rows, planted rows included."""
    index = read_index(data_dir)
    splits = assign_splits([e["company_id"] for e in index])
    out = Ledger()
    for entry in index:
        cid = entry["company_id"]
        accounts, vendors, txns = read_company(company_dir(data_dir, entry), cid)
        copies, planted = plant_duplicates(cid, txns)
        out.companies.append((cid, entry["display_name"], entry["industry_title"], splits[cid]))
        out.accounts += accounts
        out.vendors += vendors
        out.transactions += txns + copies
        out.planted_anomalies += planted
    return out


def write_ledger(conn: psycopg.Connection, ledger: Ledger) -> None:
    """Replace every ledger table with `ledger`, inside the caller's transaction."""
    conn.execute("TRUNCATE " + ", ".join(f"ledger.{t}" for t in TABLES))
    planted = [
        (a.anomaly_id, a.company_id, a.kind, Jsonb(a.txn_ids), Jsonb(a.details)) for a in ledger.planted_anomalies
    ]
    with conn.cursor() as cur:
        for table in TABLES:
            rows = planted if table == "planted_anomalies" else getattr(ledger, table)
            with cur.copy(f"COPY ledger.{table} ({COLUMNS[table]}) FROM STDIN") as copy:
                for row in rows:
                    copy.write_row(row)


def table_checksums(conn: psycopg.Connection) -> dict[str, str]:
    """md5 of every row of each ledger table in primary-key order (C collation, so independent of locale)."""
    return {
        table: conn.execute(
            f"SELECT md5(coalesce(string_agg(t::text, E'\\n' ORDER BY t.{pk} COLLATE \"C\"), '')) FROM ledger.{table} t"
        ).fetchone()[0]
        for table, pk in TABLES.items()
    }


def summarize(conn: psycopg.Connection) -> dict:
    return {
        "rows": {t: conn.execute(f"SELECT count(*) FROM ledger.{t}").fetchone()[0] for t in TABLES},
        "splits": dict(conn.execute("SELECT split, count(*) FROM ledger.companies GROUP BY split ORDER BY split")),
        "checksums": table_checksums(conn),
    }


def load(data_dir: Path, database_url: str) -> dict:
    """Parse first, then truncate and reload all ledger tables in one transaction; return the summary."""
    ledger = read_ledger(data_dir)
    with psycopg.connect(database_url) as conn:
        write_ledger(conn, ledger)
        return summarize(conn)


def planted_markdown(conn: psycopg.Connection) -> str:
    """The generated part of docs/DATASET_CHANGES.md: per-company counts and every planted row."""
    rows = conn.execute(
        "SELECT company_id, txn_ids ->> 1, details ->> 'original_txn_id', details ->> 'days_shifted'"
        " FROM ledger.planted_anomalies WHERE kind = 'duplicate_payment' ORDER BY anomaly_id COLLATE \"C\""
    ).fetchall()
    counts = Counter(cid for cid, *_ in rows)
    lines = [
        f"{len(rows)} planted rows in {len(counts)} companies.",
        "",
        "| company_id | planted rows |",
        "|---|---:|",
        *(f"| {cid} | {n} |" for cid, n in sorted(counts.items())),
        "",
        "| planted txn_id | original txn_id | date shift (days) |",
        "|---|---|---:|",
        *(f"| `{copy}` | `{original}` | +{days} |" for _, copy, original, days in rows),
    ]
    return "\n".join(lines)


def update_changes_doc(path: Path, markdown: str) -> None:
    """Replace the text between the generated-section markers of `path`."""
    head, begin, rest = path.read_text().partition(CHANGES_BEGIN)
    _, end, tail = rest.partition(CHANGES_END)
    if not (begin and end):
        raise ValueError(f"{path} has no generated-section markers")
    path.write_text(f"{head}{CHANGES_BEGIN}\n{markdown}\n{CHANGES_END}{tail}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Truncate and reload ledger.* from the raw dataset.")
    parser.add_argument("--data-dir", type=Path, required=True, help="extracted hackathon-100 directory")
    parser.add_argument("--database-url", default=get_settings().database_url)
    parser.add_argument("--changes-doc", type=Path, help="also regenerate the planted list in this DATASET_CHANGES.md")
    args = parser.parse_args(argv)
    summary = load(args.data_dir, args.database_url)
    if args.changes_doc:
        with psycopg.connect(args.database_url) as conn:
            update_changes_doc(args.changes_doc, planted_markdown(conn))
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
