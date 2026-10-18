"""Bookkeeping templates (SPEC 9.5): accrual, prepaid amortization, reclassification.

Expected answers are journal entries (verifier SPEC 9.3). Accrual and prepaid entries follow from the params, whose
accounts are checked against ledger.accounts; reclassify reads the transaction from ledger.transactions.
"""

from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, Field
from scar_schemas.models import AnswerType, ExpectedAnswer, JournalEntrySpec, JournalLine, TaskFamily

from .base import (
    CENT,
    FORBID_EXTRA,
    Answer,
    DateStr,
    Money,
    MonthStr,
    ParamError,
    Template,
    check_account,
    money,
    month_end,
    month_ends,
    month_label,
    next_month,
    parse_date,
    parse_month,
    sql_args,
)

PREPAID_ACCOUNT = "acct-prepaid-expenses"  # added to every company by the loader (docs/DECISIONS.md)
# At most 6 entries, so a prepaid task fits the agent's MAX_STEPS even with one tool call per step.
PREPAID_MONTHS = (2, 3, 4, 6)
# Expenses a business actually prepays; prepaid tasks amortize into one of these (by account name).
PREPAID_EXPENSES = {
    "Advertising and Marketing",
    "Equipment Rental",
    "Insurance",
    "Legal and Professional Fees",
    "Operating Expense",
    "Rent and Lease",
    "Taxes and Licenses",
}
# Operating accounts a bill can plausibly be reclassified into (by account name); excludes COGS, wages,
# depreciation, interest, income tax and bad debts, which a vendor bill is never moved into.
RECLASS_TARGETS = {
    "Advertising and Marketing",
    "Equipment Rental",
    "Insurance",
    "Job Materials",
    "Legal and Professional Fees",
    "Operating Expense",
    "Rent and Lease",
    "Repairs and Maintenance",
    "Shipping and Delivery",
    "Subcontractors",
    "Taxes and Licenses",
    "Utilities",
}
# The accrual sampler credits the liability an accountant would use (by account name, the same in every company);
# any other expense is accrued to Accounts Payable. These expenses are never accrued to a payable.
ACCRUAL_LIABILITY = {
    "Wage Expense": "Wages Payable",
    "Project Manager Wages": "Wages Payable",
    "Wages": "Wages Payable",
    "Interest Expense": "Accrued Interest",
    "Income Tax Expense": "Income Tax Payable",
}
NOT_ACCRUED = {"Bad Debts", "Cost of Goods Sold", "Depreciation"}


def _entry(entry_date: str, debit_account: str, credit_account: str, amount: Decimal) -> JournalEntrySpec:
    return JournalEntrySpec(
        entry_date=entry_date,
        lines=[
            JournalLine(account_id=debit_account, debit=amount),
            JournalLine(account_id=credit_account, credit=amount),
        ],
    )


def _entries_answer(entries: list[JournalEntrySpec]) -> Answer:
    return Answer(ExpectedAnswer(answer_type=AnswerType.journal_entry, journal_entries=entries))


def _cents(lo: int, hi: int, rng) -> str:
    return f"{Decimal(rng.randint(lo, hi)) / 100:.2f}"


class AccrualParams(BaseModel):
    model_config = FORBID_EXTRA
    expense_account_id: str = Field(description="Expense account to debit (account_id from get_accounts).")
    liability_account_id: str = Field(description="Liability account to credit (account_id from get_accounts).")
    amount: Money = Field(description="Amount to accrue, in dollars.")
    entry_date: DateStr = Field(description="Entry date, YYYY-MM-DD.")


class PrepaidParams(BaseModel):
    model_config = FORBID_EXTRA
    prepaid_account_id: str = Field(description="Prepaid asset account to credit (account_id from get_accounts).")
    expense_account_id: str = Field(description="Expense account to debit (account_id from get_accounts).")
    total: Money = Field(description="Total prepaid amount to amortize, in dollars.")
    months: int = Field(ge=2, le=24, description="Number of monthly entries.")
    start_month: MonthStr = Field(description="Month of the first entry, YYYY-MM.")


class ReclassifyParams(BaseModel):
    model_config = FORBID_EXTRA
    txn_id: str = Field(description="ID of the transaction to move (txn_id).")
    to_account_id: str = Field(description="Account to move it to (account_id from get_accounts).")


class Accrual(Template):
    template_id = "book.accrual"
    family = TaskFamily.bookkeeping
    answer_type = AnswerType.journal_entry
    description = "Record an accrual journal entry that debits an expense account and credits a liability account."
    difficulty = 1
    difficulties = (1,)
    Params = AccrualParams
    phrasings = (
        "Record an accrual of {amount} for {expense_account}, crediting {liability_account}, dated {entry_date}.",
        "On {entry_date}, accrue {amount} owed for {expense_account}, recording the liability in {liability_account}.",
        "Book a journal entry dated {entry_date} that debits {expense_account} and credits {liability_account}"
        " for {amount}.",
        "We owe {amount} for {expense_account} that has not been billed yet. Accrue it on {entry_date} against"
        " {liability_account}.",
        "Create the accrual entry for {amount} of {expense_account}, with {liability_account} as the liability,"
        " on {entry_date}.",
    )

    def sample(self, conn, company, rng, difficulty):
        liabilities = {company.account_name(a): a for a in company.accounts_of_type("liability")}
        pairs = []
        for expense in company.accounts_of_type("expense"):
            name = company.account_name(expense)
            liability = liabilities.get(ACCRUAL_LIABILITY.get(name, "Accounts Payable"))
            if name not in NOT_ACCRUED and liability:
                pairs.append((expense, liability))
        dates = month_ends(company)
        if not (pairs and dates):
            return None
        expense, liability = rng.choice(pairs)
        return {
            "expense_account_id": expense,
            "liability_account_id": liability,
            "amount": _cents(5_000, 500_000, rng),
            "entry_date": rng.choice(dates).isoformat(),
        }

    def check(self, conn, company, p):
        check_account(company, p, "expense_account_id", "expense")
        check_account(company, p, "liability_account_id", "liability")
        parse_date(p, "entry_date")

    def answer(self, conn, company, p):
        entry = _entry(p["entry_date"], p["expense_account_id"], p["liability_account_id"], Decimal(p["amount"]))
        return _entries_answer([entry])

    def placeholders(self, company, p):
        return {
            "amount": money(p["amount"]),
            "expense_account": company.account_name(p["expense_account_id"]),
            "liability_account": company.account_name(p["liability_account_id"]),
            "entry_date": p["entry_date"],
        }

    def interpretation(self, company, p):
        ph = self.placeholders(company, p)
        return (
            f"One journal entry at {company.name} dated {ph['entry_date']} that accrues {ph['amount']} of"
            f" {ph['expense_account']} against {ph['liability_account']}."
        )


class PrepaidAmortization(Template):
    template_id = "book.prepaid_amortization"
    family = TaskFamily.bookkeeping
    answer_type = AnswerType.journal_entry
    description = "Record monthly journal entries that amortize a prepaid expense into an expense account over months."
    difficulty = 2
    difficulties = (2,)
    Params = PrepaidParams
    phrasings = (
        "We prepaid {total}, recorded in {prepaid_account}. Amortize it into {expense_account} over {months} months"
        " starting {start_month}: one entry on the last day of each month, each month's amount rounded to cents,"
        " with the final month absorbing any rounding difference.",
        "Expense the {total} balance in {prepaid_account} to {expense_account} evenly over {months} months beginning"
        " in {start_month}. Post one entry on each month's last day; round to cents and put any rounding difference"
        " in the last month.",
        "Record the monthly amortization of a {total} prepayment from {prepaid_account} to {expense_account} for"
        " {months} months, starting {start_month}. Date each entry on the last day of its month, round each amount"
        " to cents, and let the last month take the remainder.",
        "Starting in {start_month}, move {total} from {prepaid_account} into {expense_account} in {months} equal"
        " monthly entries on each month-end (rounded to cents; the final entry absorbs rounding).",
        "Set up {months} month-end journal entries, starting {start_month}, that amortize {total} of"
        " {prepaid_account} into {expense_account}. Split it evenly, rounded to cents, with any leftover cents in"
        " the final month.",
    )

    def sample(self, conn, company, rng, difficulty):
        prepaid = f"{company.company_id}:{PREPAID_ACCOUNT}"
        expense = [a for a in company.accounts_of_type("expense") if company.account_name(a) in PREPAID_EXPENSES]
        ends = month_ends(company)
        choices = [m for m in PREPAID_MONTHS if m <= len(ends)]
        if prepaid not in company.accounts or not expense or not choices:
            return None
        months = rng.choice(choices)
        return {
            "prepaid_account_id": prepaid,
            "expense_account_id": rng.choice(expense),
            "total": _cents(60_000, 2_400_000, rng),
            "months": months,
            "start_month": ends[rng.randint(0, len(ends) - months)].strftime("%Y-%m"),
        }

    @staticmethod
    def amounts(total: Decimal, months: int) -> list[Decimal]:
        """total / months rounded half up to cents; the last month absorbs the rounding difference."""
        per = (total / months).quantize(CENT, rounding=ROUND_HALF_UP)
        return [per] * (months - 1) + [total - per * (months - 1)]

    def check(self, conn, company, p):
        check_account(company, p, "prepaid_account_id", "asset")
        check_account(company, p, "expense_account_id", "expense")
        parse_month(p, "start_month")
        if min(self.amounts(Decimal(p["total"]), p["months"])) <= 0:
            raise ParamError(f"total: {money(p['total'])} is too small to split over {p['months']} months")

    def answer(self, conn, company, p):
        entries, month = [], parse_month(p, "start_month")
        for amount in self.amounts(Decimal(p["total"]), p["months"]):
            entry_date = month_end(month).isoformat()
            entries.append(_entry(entry_date, p["expense_account_id"], p["prepaid_account_id"], amount))
            month = next_month(month)
        return _entries_answer(entries)

    def placeholders(self, company, p):
        return {
            "total": money(p["total"]),
            "prepaid_account": company.account_name(p["prepaid_account_id"]),
            "expense_account": company.account_name(p["expense_account_id"]),
            "months": str(p["months"]),
            "start_month": month_label(parse_month(p, "start_month")),
        }

    def interpretation(self, company, p):
        ph = self.placeholders(company, p)
        return (
            f"{ph['months']} month-end journal entries at {company.name}, starting {ph['start_month']}, that amortize"
            f" {ph['total']} from {ph['prepaid_account']} into {ph['expense_account']}, rounded to cents with the last"
            " month absorbing rounding."
        )


class Reclassify(Template):
    template_id = "book.reclassify"
    family = TaskFamily.bookkeeping
    answer_type = AnswerType.journal_entry
    description = "Record a journal entry that moves an existing transaction from its current account to another one."
    difficulty = 3
    difficulties = (3,)
    Params = ReclassifyParams
    sql = (
        """SELECT txn_date, amount, account_id
FROM ledger.transactions
WHERE company_id = %(company_id)s AND txn_id = %(txn_id)s""",
    )
    # A transaction has no human name, and vendor + date + amount is not unique in this ledger, so prompts show the
    # txn_id. The agent still has to look up its date, amount and current account.
    phrasings = (
        "Transaction {txn_id} was posted to the wrong account. Reclassify it to {to_account} with one journal entry"
        " dated on the transaction's date.",
        "Move transaction {txn_id} out of its current account and into {to_account}. Record one journal entry on the"
        " transaction's date for its full amount.",
        "Record a reclassification entry, dated the same day as transaction {txn_id}, that moves its full amount to"
        " {to_account}.",
        "Transaction {txn_id} belongs in {to_account}. Post one journal entry on its date to reclassify the whole"
        " amount there.",
        "Correct the coding of transaction {txn_id}: reclassify its full amount from its current account to"
        " {to_account} in a single journal entry dated on the transaction date.",
    )

    def sample(self, conn, company, rng, difficulty):
        rows = conn.execute(
            "SELECT txn_id, account_id FROM ledger.transactions"
            " WHERE company_id = %s AND txn_type = 'bill' AND amount > 0 ORDER BY txn_id",
            (company.company_id,),
        ).fetchall()
        if not rows:
            return None
        txn_id, account = rng.choice(rows)
        targets = [
            a
            for a in company.accounts_of_type("expense")
            if a != account and company.account_name(a) in RECLASS_TARGETS
        ]
        return {"txn_id": txn_id, "to_account_id": rng.choice(targets)} if targets else None

    def check(self, conn, company, p):
        row = conn.execute(self.sql[0], sql_args(company, p)).fetchone()
        if row is None:
            raise ParamError(f"txn_id: {p['txn_id']} is not a transaction of {company.company_id}")
        if row[1] <= 0:
            raise ParamError(f"txn_id: {p['txn_id']} has amount {row[1]}; only positive amounts can be reclassified")
        check_account(company, p, "to_account_id")
        if p["to_account_id"] == row[2]:
            raise ParamError(f"to_account_id: {p['txn_id']} is already in {company.account_name(row[2])}")

    def answer(self, conn, company, p):
        txn_date, amount, account_id = conn.execute(self.sql[0], sql_args(company, p)).fetchone()
        return _entries_answer([_entry(txn_date.isoformat(), p["to_account_id"], account_id, amount)])

    def placeholders(self, company, p):
        return {"txn_id": p["txn_id"], "to_account": company.account_name(p["to_account_id"])}

    def interpretation(self, company, p):
        return (
            f"One journal entry at {company.name}, dated on transaction {p['txn_id']}'s date, that moves its full"
            f" amount from its current account to {company.account_name(p['to_account_id'])}."
        )
