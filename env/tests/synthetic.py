"""A small deterministic ledger in canonical form (SPEC 8.7) for tests that must not depend on the real dataset.

IDs follow docs/DATASET.md: account/vendor IDs are `{company_id}:{raw id}`, txn IDs `{company_id}:{doc id}:{line}`.
Tests should compute expected values from these Python rows, independently of the SQL under test.

Known contents per company (2026-01-01 .. 2026-06-30):
- rent bill on the 1st of each month (a multiple of 100), utilities bill mid-month, freight bill every Monday,
  insurance bill each quarter (1200.00), wage and COGS journal lines without a vendor;
- one negative wage reclassification (-262.50, txn_type wip_capitalization);
- one planted duplicate freight bill 2 days after an original (recorded in `planted_anomalies`);
- one freight outlier far above 3x the freight vendor's median.
"""

import random
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import psycopg

COMPANIES = [
    ("company-901", "Alpha Supply Co", "Industrial Supplies Merchant Wholesalers", "train"),
    ("company-902", "Beta Design Studio", "Interior Design Services", "train"),
    ("company-903", "Gamma Eval Bakery", "Retail Bakeries", "eval"),
]

ACCOUNTS = [
    ("acct-checking", "Checking", "asset"),
    ("acct-inventory", "Inventory", "asset"),
    ("acct-prepaid-expenses", "Prepaid Expenses", "asset"),
    ("acct-accounts-payable", "Accounts Payable", "liability"),
    ("acct-wages-payable", "Wages Payable", "liability"),
    ("acct-accrued-interest", "Accrued Interest", "liability"),
    ("acct-owner-equity", "Owner's Equity", "equity"),
    ("acct-product-income", "Product Sales", "income"),
    ("acct-rent", "Rent and Lease", "expense"),
    ("acct-utilities", "Utilities", "expense"),
    ("acct-freight", "Freight and Delivery", "expense"),
    ("acct-wages", "Wages", "expense"),
    ("acct-cogs", "Cost of Goods Sold", "expense"),
    ("acct-insurance", "Insurance", "expense"),
]

VENDORS = [
    ("vend-landlord", "Oakline Properties"),
    ("vend-electricity", "Bright Grid Utilities"),
    ("vend-freight-vendor", "Swift Haul Logistics"),
    ("vend-insurer", "Shield Mutual Insurance"),
]

START = date(2026, 1, 1)
END = date(2026, 6, 30)


@dataclass(frozen=True)
class Txn:
    txn_id: str
    company_id: str
    txn_date: date
    amount: Decimal
    account_id: str
    vendor_id: str | None
    description: str
    txn_type: str


@dataclass(frozen=True)
class PlantedAnomaly:
    anomaly_id: str
    company_id: str
    kind: str
    txn_ids: list[str]
    details: dict


@dataclass(frozen=True)
class SyntheticLedger:
    companies: list[tuple[str, str, str, str]]  # company_id, name, industry, split
    accounts: list[tuple[str, str, str, str]]  # account_id, company_id, name, account_type
    vendors: list[tuple[str, str, str]]  # vendor_id, company_id, name
    transactions: list[Txn]
    planted_anomalies: list[PlantedAnomaly]

    def company_txns(self, company_id: str) -> list[Txn]:
        return [t for t in self.transactions if t.company_id == company_id]


def _money(x: float) -> Decimal:
    return Decimal(f"{x:.2f}")


def _company_txns(index: int, cid: str) -> tuple[list[Txn], list[PlantedAnomaly]]:
    rng = random.Random(index)
    txns: list[Txn] = []
    bill_no = 0
    je_no = 0

    def bill(d: date, amount: Decimal, acct: str, vend: str, desc: str) -> Txn:
        nonlocal bill_no
        bill_no += 1
        t = Txn(f"{cid}:bill-{bill_no:05d}:1", cid, d, amount, f"{cid}:{acct}", f"{cid}:{vend}", desc, "bill")
        txns.append(t)
        return t

    def je(d: date, amount: Decimal, acct: str, desc: str, txn_type: str) -> Txn:
        nonlocal je_no
        je_no += 1
        t = Txn(f"{cid}:je-{je_no:05d}:1", cid, d, amount, f"{cid}:{acct}", None, desc, txn_type)
        txns.append(t)
        return t

    rent = Decimal("2500.00") if index == 0 else Decimal("1800.00") + Decimal(100 * index)
    freight_bills: list[Txn] = []
    d = START
    while d <= END:
        if d.day == 1:
            bill(d, rent, "acct-rent", "vend-landlord", "Monthly rent.")
            if d.month in (1, 4):
                bill(d, Decimal("1200.00"), "acct-insurance", "vend-insurer", "Quarterly insurance premium.")
        if d.day == 15:
            bill(d, _money(rng.uniform(200, 450)), "acct-utilities", "vend-electricity", "Electricity service.")
        if d.weekday() == 0:
            freight_bills.append(
                bill(d, _money(rng.uniform(80, 400)), "acct-freight", "vend-freight-vendor", "Outbound freight.")
            )
        if d.weekday() == 4 and (d - START).days % 14 < 7:
            je(d, _money(rng.uniform(3000, 3500)), "acct-wages", "Biweekly wages earned.", "wage_accrual")
        if d.weekday() == 2:
            je(d, _money(rng.uniform(150, 900)), "acct-cogs", "Cost of goods delivered.", "cost_of_goods")
        d += timedelta(days=1)

    je(date(2026, 3, 18), Decimal("-262.50"), "acct-wages", "Manager time moved to WIP.", "wip_capitalization")

    outlier = bill(date(2026, 5, 20), Decimal("2950.37"), "acct-freight", "vend-freight-vendor", "Expedited freight.")

    original = freight_bills[5 + index]
    dup = Txn(
        f"{cid}:planted-dup-0001:1",
        cid,
        original.txn_date + timedelta(days=2),
        original.amount,
        original.account_id,
        original.vendor_id,
        original.description,
        original.txn_type,
    )
    txns.append(dup)
    planted = [
        PlantedAnomaly(
            f"{cid}:planted-dup-0001",
            cid,
            "duplicate_payment",
            [original.txn_id, dup.txn_id],
            {"original_txn_id": original.txn_id, "days_shifted": 2},
        )
    ]
    assert outlier.txn_id not in {original.txn_id, dup.txn_id}
    return txns, planted


def build_synthetic_ledger() -> SyntheticLedger:
    accounts, vendors, txns, planted = [], [], [], []
    for index, (cid, _, _, _) in enumerate(COMPANIES):
        accounts += [(f"{cid}:{raw}", cid, name, typ) for raw, name, typ in ACCOUNTS]
        vendors += [(f"{cid}:{raw}", cid, name) for raw, name in VENDORS]
        t, p = _company_txns(index, cid)
        txns += t
        planted += p
    return SyntheticLedger(list(COMPANIES), accounts, vendors, txns, planted)


def insert_ledger(conn: psycopg.Connection, ledger: SyntheticLedger) -> None:
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO ledger.companies VALUES (%s, %s, %s, %s)", ledger.companies)
        cur.executemany("INSERT INTO ledger.accounts VALUES (%s, %s, %s, %s)", ledger.accounts)
        cur.executemany("INSERT INTO ledger.vendors VALUES (%s, %s, %s)", ledger.vendors)
        cur.executemany(
            "INSERT INTO ledger.transactions VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            [
                (t.txn_id, t.company_id, t.txn_date, t.amount, t.account_id, t.vendor_id, t.description, t.txn_type)
                for t in ledger.transactions
            ],
        )
        cur.executemany(
            "INSERT INTO ledger.planted_anomalies VALUES (%s, %s, %s, %s, %s)",
            [
                (
                    a.anomaly_id,
                    a.company_id,
                    a.kind,
                    psycopg.types.json.Jsonb(a.txn_ids),
                    psycopg.types.json.Jsonb(a.details),
                )
                for a in ledger.planted_anomalies
            ],
        )
