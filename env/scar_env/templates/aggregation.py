"""Aggregation templates (SPEC 9.5): sums, counts, top vendor, month-over-month change, share of expense.

Samplers pick expense accounts only (sums on asset or liability accounts mean "amount billed", docs/DATASET.md) and
anchor the period on a real transaction, so the period always contains at least one matching row.
"""

from decimal import Decimal

from pydantic import BaseModel, Field
from scar_schemas.models import AnswerType, ExpectedAnswer, TaskFamily

from .base import (
    ACCOUNT_DESC,
    END_DESC,
    FORBID_EXTRA,
    START_DESC,
    Answer,
    DateStr,
    MonthStr,
    ParamError,
    Period,
    Template,
    VendorPeriod,
    check_account,
    check_period,
    check_vendor,
    column,
    full_months,
    id_set_answer,
    month_label,
    parse_month,
    period_params,
    prev_month,
    require_data,
    sql_args,
    tier_period,
)

EXPENSE_ACCOUNTS_WITH_TXNS = (
    "SELECT DISTINCT t.account_id FROM ledger.transactions t JOIN ledger.accounts a ON a.account_id = t.account_id"
    " WHERE t.company_id = %s AND a.account_type = 'expense' ORDER BY t.account_id"
)


class AccountPeriod(BaseModel):
    model_config = FORBID_EXTRA
    account_id: str = Field(description=ACCOUNT_DESC)
    start_date: DateStr = Field(description=START_DESC)
    end_date: DateStr = Field(description=END_DESC)


class AccountMonth(BaseModel):
    model_config = FORBID_EXTRA
    account_id: str = Field(description=ACCOUNT_DESC)
    month: MonthStr = Field(description="Month, YYYY-MM; the answer compares it with the month before.")


def _sample_account_period(conn, company, rng, difficulty) -> dict | None:
    accounts = column(conn, EXPENSE_ACCOUNTS_WITH_TXNS, (company.company_id,))
    if not accounts:
        return None
    account = rng.choice(accounts)
    dates = column(
        conn,
        "SELECT txn_date FROM ledger.transactions WHERE company_id = %s AND account_id = %s ORDER BY txn_date, txn_id",
        (company.company_id, account),
    )
    period = period_params(tier_period(rng, company, rng.choice(dates), difficulty))
    return period and {"account_id": account, **period}


def _account_period_placeholders(company, p) -> dict[str, str]:
    return {"account": company.account_name(p["account_id"]), "start_date": p["start_date"], "end_date": p["end_date"]}


class TotalByAccountPeriod(Template):
    template_id = "agg.total_by_account_period"
    family = TaskFamily.aggregation
    answer_type = AnswerType.number
    description = "Total amount recorded in one account, such as an expense category, between two dates."
    difficulty = 1
    difficulties = (1, 2, 3)
    Params = AccountPeriod
    sql = (
        """SELECT COALESCE(SUM(amount), 0), COUNT(*)
FROM ledger.transactions
WHERE company_id = %(company_id)s AND account_id = %(account_id)s
  AND txn_date BETWEEN %(start_date)s AND %(end_date)s""",
    )
    phrasings = (
        "What is the total amount recorded in the {account} account from {start_date} to {end_date}, inclusive?",
        "How much was posted to {account} between {start_date} and {end_date} (both dates included)?",
        "Add up every {account} transaction dated {start_date} through {end_date}. What is the total?",
        "From {start_date} to {end_date} inclusive, what did {account} transactions sum to?",
        "Give the total of all transactions in {account} with dates from {start_date} to {end_date}, inclusive.",
    )

    def sample(self, conn, company, rng, difficulty):
        return _sample_account_period(conn, company, rng, difficulty)

    def check(self, conn, company, p):
        check_account(company, p, "account_id")
        check_period(company, p)

    def answer(self, conn, company, p):
        total, n = conn.execute(self.sql[0], sql_args(company, p)).fetchone()
        return Answer(ExpectedAnswer(answer_type=AnswerType.number, number=total), degenerate=n == 0)

    def placeholders(self, company, p):
        return _account_period_placeholders(company, p)

    def interpretation(self, company, p):
        account = company.account_name(p["account_id"])
        return (
            f"Total of all {company.name} transactions in {account} from {p['start_date']} to {p['end_date']},"
            " dates inclusive."
        )


class TotalByVendorPeriod(Template):
    template_id = "agg.total_by_vendor_period"
    family = TaskFamily.aggregation
    answer_type = AnswerType.number
    description = "Total amount spent with one vendor between two dates."
    difficulty = 1
    difficulties = (1, 2, 3)
    Params = VendorPeriod
    sql = (
        """SELECT COALESCE(SUM(amount), 0), COUNT(*)
FROM ledger.transactions
WHERE company_id = %(company_id)s AND vendor_id = %(vendor_id)s
  AND txn_date BETWEEN %(start_date)s AND %(end_date)s""",
    )
    phrasings = (
        "How much did we spend with {vendor} from {start_date} to {end_date}, inclusive?",
        "What is the total amount billed by {vendor} between {start_date} and {end_date} (both dates included)?",
        "Add up all {vendor} transactions dated {start_date} through {end_date}. What is the total?",
        "From {start_date} to {end_date} inclusive, what was our total spend with the vendor {vendor}?",
        "Give the total of all transactions from {vendor} with dates from {start_date} to {end_date}, inclusive.",
    )

    def sample(self, conn, company, rng, difficulty):
        vendors = column(
            conn,
            "SELECT DISTINCT vendor_id FROM ledger.transactions WHERE company_id = %s AND vendor_id IS NOT NULL"
            " ORDER BY vendor_id",
            (company.company_id,),
        )
        if not vendors:
            return None
        vendor = rng.choice(vendors)
        dates = column(
            conn,
            "SELECT txn_date FROM ledger.transactions WHERE company_id = %s AND vendor_id = %s"
            " ORDER BY txn_date, txn_id",
            (company.company_id, vendor),
        )
        period = period_params(tier_period(rng, company, rng.choice(dates), difficulty))
        return period and {"vendor_id": vendor, **period}

    def check(self, conn, company, p):
        check_vendor(company, p)
        check_period(company, p)

    def answer(self, conn, company, p):
        total, n = conn.execute(self.sql[0], sql_args(company, p)).fetchone()
        return Answer(ExpectedAnswer(answer_type=AnswerType.number, number=total), degenerate=n == 0)

    def placeholders(self, company, p):
        return {"vendor": company.vendors[p["vendor_id"]], "start_date": p["start_date"], "end_date": p["end_date"]}

    def interpretation(self, company, p):
        vendor = company.vendors[p["vendor_id"]]
        return (
            f"Total of all {company.name} transactions with vendor {vendor} from {p['start_date']} to"
            f" {p['end_date']}, dates inclusive."
        )


class CountByAccountPeriod(Template):
    template_id = "agg.count_by_account_period"
    family = TaskFamily.aggregation
    answer_type = AnswerType.number
    description = "Number of transactions in one account between two dates."
    difficulty = 1
    difficulties = (1, 2, 3)
    Params = AccountPeriod
    sql = (
        """SELECT COUNT(*)
FROM ledger.transactions
WHERE company_id = %(company_id)s AND account_id = %(account_id)s
  AND txn_date BETWEEN %(start_date)s AND %(end_date)s""",
    )
    phrasings = (
        "How many transactions were recorded in {account} from {start_date} to {end_date}, inclusive?",
        "Count the {account} transactions dated between {start_date} and {end_date} (both dates included).",
        "What is the number of transactions posted to the {account} account from {start_date} through {end_date}?",
        "From {start_date} to {end_date} inclusive, how many {account} transactions are there?",
        "How many entries hit {account} with dates from {start_date} to {end_date}, inclusive?",
    )

    def sample(self, conn, company, rng, difficulty):
        return _sample_account_period(conn, company, rng, difficulty)

    def check(self, conn, company, p):
        check_account(company, p, "account_id")
        check_period(company, p)

    def answer(self, conn, company, p):
        (n,) = conn.execute(self.sql[0], sql_args(company, p)).fetchone()
        expected = ExpectedAnswer(answer_type=AnswerType.number, number=Decimal(n), tolerance=Decimal("0"))
        return Answer(expected, degenerate=n == 0)

    def placeholders(self, company, p):
        return _account_period_placeholders(company, p)

    def interpretation(self, company, p):
        account = company.account_name(p["account_id"])
        return (
            f"Number of {company.name} transactions in {account} from {p['start_date']} to {p['end_date']},"
            " dates inclusive."
        )


class TopVendorBySpend(Template):
    template_id = "agg.top_vendor_by_spend"
    family = TaskFamily.aggregation
    answer_type = AnswerType.id_set
    description = "Which vendor had the highest total spend between two dates."
    difficulty = 2
    difficulties = (2, 3)
    Params = Period
    sql = (
        """WITH spend AS (
  SELECT vendor_id, SUM(amount) AS total
  FROM ledger.transactions
  WHERE company_id = %(company_id)s AND vendor_id IS NOT NULL
    AND txn_date BETWEEN %(start_date)s AND %(end_date)s
  GROUP BY vendor_id
)
SELECT vendor_id FROM spend WHERE total = (SELECT MAX(total) FROM spend) ORDER BY vendor_id""",
    )
    phrasings = (
        "Which vendor did we spend the most with from {start_date} to {end_date}, inclusive?"
        " If several vendors tie, list all of them.",
        "Who was our top vendor by total spend between {start_date} and {end_date} (both dates included)?"
        " Include every vendor tied for first.",
        "Find the vendor with the highest total billed amount from {start_date} through {end_date};"
        " list all vendors if there is a tie.",
        "From {start_date} to {end_date} inclusive, which vendor received the largest total amount?"
        " Give all tied vendors.",
        "Identify the vendor or vendors with the greatest total spend in transactions dated {start_date} to"
        " {end_date}, inclusive.",
    )

    def sample(self, conn, company, rng, difficulty):
        dates = column(
            conn,
            "SELECT txn_date FROM ledger.transactions WHERE company_id = %s AND vendor_id IS NOT NULL"
            " ORDER BY txn_date, txn_id",
            (company.company_id,),
        )
        if not dates:
            return None
        return period_params(tier_period(rng, company, rng.choice(dates), difficulty))

    def check(self, conn, company, p):
        check_period(company, p)

    def answer(self, conn, company, p):
        return id_set_answer(column(conn, self.sql[0], sql_args(company, p)))

    def placeholders(self, company, p):
        return {"start_date": p["start_date"], "end_date": p["end_date"]}

    def interpretation(self, company, p):
        return (
            f"The vendor or vendors with the highest total transaction amount at {company.name} from"
            f" {p['start_date']} to {p['end_date']}, dates inclusive, including all ties."
        )


class MomChangeAccount(Template):
    template_id = "agg.mom_change_account"
    family = TaskFamily.aggregation
    answer_type = AnswerType.number
    description = "Change in one account's monthly total from the previous month to a given month."
    difficulty = 2
    difficulties = (2,)
    Params = AccountMonth
    sql = (
        """SELECT COALESCE(SUM(amount) FILTER (WHERE to_char(txn_date, 'YYYY-MM') = %(month)s), 0)
     - COALESCE(SUM(amount) FILTER (WHERE to_char(txn_date, 'YYYY-MM') = %(prev_month)s), 0),
       COUNT(*) FILTER (WHERE to_char(txn_date, 'YYYY-MM') = %(month)s),
       COUNT(*) FILTER (WHERE to_char(txn_date, 'YYYY-MM') = %(prev_month)s)
FROM ledger.transactions
WHERE company_id = %(company_id)s AND account_id = %(account_id)s""",
    )
    phrasings = (
        "By how much did the {account} total change in {month} compared with {prev_month}?"
        " Give the {month} total minus the {prev_month} total.",
        "What is the month-over-month change in {account} for {month}, computed as the {month} total minus the"
        " {prev_month} total?",
        "Subtract the {prev_month} total of {account} from its {month} total. What is the result?",
        "How much more was recorded in {account} in {month} than in {prev_month}?"
        " Answer with a negative number if it went down.",
        "Report the change in {account} from {prev_month} to {month}: the {month} total minus the {prev_month} total.",
    )

    def sample(self, conn, company, rng, difficulty):
        accounts = column(conn, EXPENSE_ACCOUNTS_WITH_TXNS, (company.company_id,))
        months = full_months(company)[1:]  # the previous month must be fully inside the data range too
        if not accounts or not months:
            return None
        return {"account_id": rng.choice(accounts), "month": rng.choice(months).strftime("%Y-%m")}

    def check(self, conn, company, p):
        check_account(company, p, "account_id")
        month = parse_month(p, "month")
        first, last = require_data(company)
        if month > last or month <= first.replace(day=1):
            raise ParamError(
                f"month: {p['month']} needs data for it and the month before; {company.company_id}'s data runs"
                f" {first} to {last}"
            )

    def answer(self, conn, company, p):
        prev = prev_month(parse_month(p, "month")).strftime("%Y-%m")
        change, n_month, n_prev = conn.execute(self.sql[0], sql_args(company, p, prev_month=prev)).fetchone()
        return Answer(ExpectedAnswer(answer_type=AnswerType.number, number=change), degenerate=not (n_month and n_prev))

    def placeholders(self, company, p):
        month = parse_month(p, "month")
        return {
            "account": company.account_name(p["account_id"]),
            "month": month_label(month),
            "prev_month": month_label(prev_month(month)),
        }

    def interpretation(self, company, p):
        ph = self.placeholders(company, p)
        return f"The {ph['month']} total of {ph['account']} at {company.name} minus its {ph['prev_month']} total."


class ShareOfExpense(Template):
    template_id = "agg.share_of_expense"
    family = TaskFamily.aggregation
    answer_type = AnswerType.number
    description = "One expense account's percentage share of total expenses between two dates."
    difficulty = 3
    difficulties = (3,)
    Params = AccountPeriod
    sql = (
        """SELECT ROUND(100 * COALESCE(SUM(t.amount) FILTER (WHERE t.account_id = %(account_id)s), 0)
                   / NULLIF(SUM(t.amount), 0), 2),
       COUNT(*) FILTER (WHERE t.account_id = %(account_id)s),
       SUM(t.amount)
FROM ledger.transactions t
JOIN ledger.accounts a ON a.account_id = t.account_id
WHERE t.company_id = %(company_id)s AND a.account_type = 'expense'
  AND t.txn_date BETWEEN %(start_date)s AND %(end_date)s""",
    )
    phrasings = (
        "What percentage of total expenses from {start_date} to {end_date} (inclusive) was {account}?"
        " Round to 2 decimal places.",
        "Between {start_date} and {end_date}, both included, what share of all expense-account spending went to"
        " {account}? Answer as a percentage rounded to 2 decimals.",
        "{account} made up what percent of total expenses from {start_date} through {end_date}?"
        " Give a percentage such as 12.34, rounded to 2 decimals.",
        "Compute {account} as a percentage of all expense accounts combined for {start_date} to {end_date},"
        " inclusive, rounded to two decimal places.",
        "From {start_date} to {end_date} inclusive, what percent of total expense did {account} account for?"
        " Round to 2 decimals.",
    )

    def sample(self, conn, company, rng, difficulty):
        return _sample_account_period(conn, company, rng, difficulty)

    def check(self, conn, company, p):
        check_account(company, p, "account_id", "expense")
        check_period(company, p)

    def answer(self, conn, company, p):
        share, n, total = conn.execute(self.sql[0], sql_args(company, p)).fetchone()
        if total is None or total <= 0:
            raise ParamError(f"total expenses from {p['start_date']} to {p['end_date']} are not positive")
        expected = ExpectedAnswer(answer_type=AnswerType.number, number=share, tolerance=Decimal("0.05"))
        return Answer(expected, degenerate=n == 0)

    def placeholders(self, company, p):
        return _account_period_placeholders(company, p)

    def interpretation(self, company, p):
        account = company.account_name(p["account_id"])
        return (
            f"{account} as a percentage of all expense-account transactions at {company.name} from"
            f" {p['start_date']} to {p['end_date']}, dates inclusive, rounded to 2 decimals."
        )
