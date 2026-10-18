"""The 30 seed tasks (P1-05): hand-written prompts on train-split companies, answers computed by template SQL.

    uv run python -m scar_env.seed_tasks build [--force]   # write fixtures/tasks_seed.jsonl from SEEDS (new task IDs)
    uv run python -m scar_env.seed_tasks load              # verify against the ledger, then insert into app.tasks

`build` runs once; the committed JSONL fixes the task IDs other people's fixtures refer to, so it refuses to overwrite
without --force. `load` recomputes every expected answer from the current ledger and refuses to store a task whose
answer drifted (for example after a ledger reload with a changed loader); it skips tasks already stored.
"""

import argparse
import json
import random
from pathlib import Path

import psycopg
from scar_schemas.guards import assert_train_only
from scar_schemas.models import Origin, Task

from .config import REPO_ROOT, get_settings
from .store import get_task, store_task
from .tasks import make_task

FIXTURE = REPO_ROOT / "fixtures" / "tasks_seed.jsonl"

# (template_id, company_id, difficulty, params, prompt). Params were sampled from the real ledger, then the prompts
# were written by hand to say the same thing in a bookkeeper's words. 10 per family, all train-split companies.
SEEDS: list[tuple[str, str, int, dict, str]] = [
    # --- aggregation ---
    (
        "agg.total_by_account_period",
        "company-004",
        1,
        {"account_id": "company-004:acct-cogs", "start_date": "2026-05-04", "end_date": "2026-05-26"},
        "How much did we record in Cost of Goods Sold between May 4 and May 26, 2026, counting both days?",
    ),
    (
        "agg.total_by_account_period",
        "company-029",
        2,
        {"account_id": "company-029:acct-repairs", "start_date": "2026-08-18", "end_date": "2026-10-03"},
        "What is the total of all Repairs and Maintenance transactions from 2026-08-18 through 2026-10-03, inclusive?",
    ),
    (
        "agg.total_by_vendor_period",
        "company-030",
        1,
        {"vendor_id": "company-030:vend-insurer", "start_date": "2026-05-01", "end_date": "2026-05-28"},
        "What did we spend with Rasmussen Insurance Agency from May 1 to May 28, 2026, inclusive?",
    ),
    (
        "agg.total_by_vendor_period",
        "company-046",
        3,
        {"vendor_id": "company-046:vend-electrical", "start_date": "2025-11-12", "end_date": "2026-06-07"},
        "Add up every transaction with Lowry Electrical Contractors dated from 2025-11-12 to 2026-06-07, both dates"
        " included. What is the total?",
    ),
    (
        "agg.count_by_account_period",
        "company-075",
        1,
        {"account_id": "company-075:acct-utilities", "start_date": "2026-01-01", "end_date": "2026-01-13"},
        "How many Utilities transactions were recorded from January 1 through January 13, 2026?",
    ),
    (
        "agg.count_by_account_period",
        "company-089",
        2,
        {"account_id": "company-089:acct-operating-expense", "start_date": "2026-05-06", "end_date": "2026-06-25"},
        "Count the transactions posted to Operating Expense between 2026-05-06 and 2026-06-25, including both end"
        " dates.",
    ),
    (
        "agg.top_vendor_by_spend",
        "company-053",
        2,
        {"start_date": "2026-03-03", "end_date": "2026-04-22"},
        "Which vendor did we spend the most with between March 3 and April 22, 2026, inclusive? If several vendors tie"
        " for the top spot, list all of them.",
    ),
    (
        "agg.top_vendor_by_spend",
        "company-012",
        3,
        {"start_date": "2025-12-07", "end_date": "2026-06-28"},
        "From 2025-12-07 to 2026-06-28 inclusive, which vendor received the largest total amount from us? Include"
        " every vendor tied for first.",
    ),
    (
        "agg.mom_change_account",
        "company-071",
        2,
        {"account_id": "company-071:acct-wage-expense", "month": "2026-03"},
        "By how much did Wage Expense change from February 2026 to March 2026? Give March's total minus February's"
        " total, negative if it went down.",
    ),
    (
        "agg.share_of_expense",
        "company-099",
        3,
        {"account_id": "company-099:acct-rent", "start_date": "2025-12-01", "end_date": "2026-10-03"},
        "What percentage of our total expenses was Rent and Lease from 2025-12-01 through 2026-10-03, inclusive? Use"
        " every expense account for the total and round to two decimal places.",
    ),
    # --- anomaly ---
    (
        "anom.duplicate_payments",
        "company-005",
        2,
        {"start_date": "2025-12-28", "end_date": "2026-03-06"},
        "We may have paid some bills twice. Between 2025-12-28 and 2026-03-06, find every pair of transactions with"
        " the same vendor and the same amount dated at most 3 days apart, both inside that window, and list the"
        " transaction ID of the later one in each pair (on the same day, the larger transaction ID counts as later).",
    ),
    (
        "anom.duplicate_payments",
        "company-024",
        2,
        {"start_date": "2026-03-28", "end_date": "2026-04-03"},
        "Check for duplicate payments from March 28 to April 3, 2026: same vendor, same amount, no more than three"
        " days apart, with both transactions in that range. Give the transaction ID of the later transaction of each"
        " pair; if both fall on the same day, the larger transaction ID is the later one.",
    ),
    (
        "anom.duplicate_payments",
        "company-047",
        2,
        {"start_date": "2026-03-31", "end_date": "2026-04-24"},
        "List the IDs of likely duplicate transactions dated 2026-03-31 through 2026-04-24. A duplicate is the later"
        " of two transactions with the same vendor and amount that are 3 or fewer days apart, both within the period;"
        " for a same-day pair, the later one is the one with the larger transaction ID.",
    ),
    (
        "anom.duplicate_payments",
        "company-081",
        2,
        {"start_date": "2026-02-02", "end_date": "2026-04-17"},
        "Audit 2026-02-02 to 2026-04-17 for double payments. For each pair of same-vendor, same-amount transactions"
        " within 3 days of each other (both in the period), report the later transaction's ID, treating the larger"
        " transaction ID as later when the dates are equal.",
    ),
    (
        "anom.round_amounts_over",
        "company-006",
        1,
        {"threshold": "250.00", "start_date": "2025-10-17", "end_date": "2025-10-27"},
        "Which transactions from October 17 to October 27, 2025, inclusive, have a round amount, meaning an exact"
        " multiple of $100, of at least $250.00? List their transaction IDs.",
    ),
    (
        "anom.round_amounts_over",
        "company-004",
        1,
        {"threshold": "100.00", "start_date": "2026-06-24", "end_date": "2026-07-22"},
        "List the transaction IDs of every transaction dated 2026-06-24 through 2026-07-22 whose amount is an exact"
        " multiple of $100 and at least $100.00.",
    ),
    (
        "anom.round_amounts_over",
        "company-092",
        1,
        {"threshold": "100.00", "start_date": "2026-08-27", "end_date": "2026-09-13"},
        "Flag round-dollar transactions between August 27 and September 13, 2026, inclusive: amounts of $100.00 or"
        " more that are whole multiples of $100. Which transaction IDs qualify?",
    ),
    (
        "anom.vendor_outliers",
        "company-004",
        3,
        {"vendor_id": "company-004:vend-operating-vendor", "start_date": "2026-01-24", "end_date": "2026-01-31"},
        "Which transactions with Adeyemi & Daughters Nursery between 2026-01-24 and 2026-01-31, inclusive, were more"
        " than three times that vendor's median transaction amount? Take the median over all of the vendor's"
        " transactions in the ledger. List the transaction IDs.",
    ),
    (
        "anom.vendor_outliers",
        "company-031",
        3,
        {"vendor_id": "company-031:vend-materials", "start_date": "2026-05-29", "end_date": "2026-09-27"},
        "Find unusually large charges from Bellrock Stationers LLC dated May 29 to September 27, 2026, inclusive: any"
        " transaction above 3 times the median amount of all Bellrock Stationers LLC transactions in the whole"
        " ledger. Give their transaction IDs.",
    ),
    (
        "anom.vendor_outliers",
        "company-056",
        3,
        {"vendor_id": "company-056:vend-government", "start_date": "2026-09-24", "end_date": "2026-10-05"},
        "Between 2026-09-24 and 2026-10-05, inclusive, which Northfield Department of Revenue transactions exceed"
        " three times that vendor's median transaction amount across the entire ledger? List the transaction IDs.",
    ),
    # --- bookkeeping ---
    (
        "book.accrual",
        "company-059",
        1,
        {
            "expense_account_id": "company-059:acct-income-tax",
            "liability_account_id": "company-059:acct-tax-payable",
            "amount": "3184.91",
            "entry_date": "2026-06-30",
        },
        "Record an accrual dated 2026-06-30 for $3,184.91 of income tax we owe: debit Income Tax Expense and credit"
        " Income Tax Payable.",
    ),
    (
        "book.accrual",
        "company-073",
        1,
        {
            "expense_account_id": "company-073:acct-manager-wages",
            "liability_account_id": "company-073:acct-wages-payable",
            "amount": "4178.47",
            "entry_date": "2026-02-28",
        },
        "Book a journal entry dated February 28, 2026 accruing $4,178.47 of unpaid Project Manager Wages to Wages"
        " Payable.",
    ),
    (
        "book.accrual",
        "company-097",
        1,
        {
            "expense_account_id": "company-097:acct-marketing",
            "liability_account_id": "company-097:acct-payable",
            "amount": "1796.28",
            "entry_date": "2026-06-30",
        },
        "We owe $1,796.28 for advertising that hasn't been billed yet. Record the accrual on 2026-06-30, debiting"
        " Advertising and Marketing and crediting Accounts Payable.",
    ),
    (
        "book.accrual",
        "company-040",
        1,
        {
            "expense_account_id": "company-040:acct-permits",
            "liability_account_id": "company-040:acct-payable",
            "amount": "2120.54",
            "entry_date": "2026-09-30",
        },
        "On 2026-09-30, accrue $2,120.54 of Taxes and Licenses expense against Accounts Payable in a single journal"
        " entry.",
    ),
    (
        "book.prepaid_amortization",
        "company-078",
        2,
        {
            "prepaid_account_id": "company-078:acct-prepaid-expenses",
            "expense_account_id": "company-078:acct-professional-fees",
            "total": "22718.26",
            "months": 2,
            "start_month": "2026-07",
        },
        "We prepaid $22,718.26 for legal services, booked to Prepaid Expenses. Expense it to Legal and Professional"
        " Fees evenly over 2 months starting July 2026, with one entry on the last day of each month. Round each"
        " month to cents and let the last month take any rounding difference.",
    ),
    (
        "book.prepaid_amortization",
        "company-044",
        2,
        {
            "prepaid_account_id": "company-044:acct-prepaid-expenses",
            "expense_account_id": "company-044:acct-equipment-rental",
            "total": "10105.29",
            "months": 6,
            "start_month": "2025-10",
        },
        "A $10,105.29 equipment rental prepayment sits in Prepaid Expenses. Amortize it into Equipment Rental over 6"
        " months beginning October 2025: one journal entry dated the last day of each month, each amount rounded to"
        " cents, with the final month absorbing the rounding.",
    ),
    (
        "book.prepaid_amortization",
        "company-088",
        2,
        {
            "prepaid_account_id": "company-088:acct-prepaid-expenses",
            "expense_account_id": "company-088:acct-insurance",
            "total": "22402.39",
            "months": 3,
            "start_month": "2026-05",
        },
        "Our $22,402.39 insurance premium was prepaid into Prepaid Expenses. Recognize it in Insurance over 3 months"
        " starting May 2026, posting one entry at each month-end. Round to cents; any leftover cents go in the last"
        " month.",
    ),
    (
        "book.reclassify",
        "company-021",
        3,
        {"txn_id": "company-021:bill-variable-services-03034:1", "to_account_id": "company-021:acct-freight"},
        "Transaction company-021:bill-variable-services-03034:1 was posted to the wrong account. Record one journal"
        " entry on that transaction's date that moves its full amount out of its current account and into Shipping"
        " and Delivery.",
    ),
    (
        "book.reclassify",
        "company-055",
        3,
        {"txn_id": "company-055:pbill-00215:1", "to_account_id": "company-055:acct-freight"},
        "Reclassify transaction company-055:pbill-00215:1 to Shipping and Delivery: one journal entry, dated the same"
        " day as the transaction, for its whole amount, taking it out of the account it was originally recorded in.",
    ),
    (
        "book.reclassify",
        "company-016",
        3,
        {"txn_id": "company-016:bill-freight-02721:1", "to_account_id": "company-016:acct-subcontractors"},
        "Move transaction company-016:bill-freight-02721:1 into Subcontractors. Post a single journal entry on the"
        " transaction's date that shifts the full amount from its current account.",
    ),
]


def build_tasks(conn: psycopg.Connection) -> list[Task]:
    """Tasks for SEEDS with answers computed by the templates; prompts are the hand-written ones."""
    return [
        make_task(
            conn,
            template_id,
            company_id,
            params,
            origin=Origin.seed,
            difficulty=difficulty,
            rng=random.Random(0),
            prompt=prompt,
        )
        for template_id, company_id, difficulty, params, prompt in SEEDS
    ]


def read_fixture(path: Path = FIXTURE) -> list[Task]:
    return [Task.model_validate_json(line) for line in path.read_text().splitlines() if line.strip()]


def write_fixture(tasks: list[Task], path: Path = FIXTURE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(t.model_dump(mode="json"), sort_keys=True) + "\n" for t in tasks))


def load_tasks(conn: psycopg.Connection, tasks: list[Task]) -> int:
    """Verify every task against the current ledger, then insert the ones not stored yet. Returns how many were
    inserted. Raises before writing anything if a company is not train-split or an answer drifted."""
    for task in tasks:
        fresh = make_task(
            conn,
            task.template_id,
            task.company_id,
            task.params,
            origin=task.origin,
            difficulty=task.difficulty,
            rng=random.Random(0),
            prompt=task.prompt,
        )
        if fresh.split != "train":
            raise RuntimeError(f"seed task {task.task_id} is on a {fresh.split.value}-split company")
        if fresh.expected != task.expected:
            raise RuntimeError(f"seed task {task.task_id}: expected answer drifted from the ledger")
    inserted = 0
    with conn.transaction():
        for task in tasks:
            existing = get_task(conn, task.task_id)
            if existing is None:
                store_task(conn, task)
                inserted += 1
            elif existing != task:
                raise RuntimeError(f"seed task {task.task_id} is stored with different content")
        assert_train_only([t.task_id for t in tasks], conn)
    return inserted


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build or load the seed tasks (P1-05).")
    parser.add_argument("command", choices=["build", "load"])
    parser.add_argument("--database-url", default=get_settings().database_url)
    parser.add_argument("--force", action="store_true", help="build: overwrite the fixture (new task IDs)")
    args = parser.parse_args(argv)
    with psycopg.connect(args.database_url, autocommit=True) as conn:
        if args.command == "build":
            if FIXTURE.exists() and not args.force:
                raise SystemExit(f"{FIXTURE} exists; task IDs are shared with other fixtures (use --force)")
            tasks = build_tasks(conn)
            write_fixture(tasks)
            print(f"wrote {len(tasks)} seed tasks to {FIXTURE}")
        else:
            inserted = load_tasks(conn, read_fixture())
            print(f"inserted {inserted} seed tasks into app.tasks")


if __name__ == "__main__":
    main()
