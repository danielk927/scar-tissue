"""Anomaly templates (SPEC 9.5): duplicate payments, round amounts, vendor outliers.

The SPEC 9.5 definitions are kept as is (docs/DECISIONS.md). Each sampler lists every match over the company's whole
data range, returns None (skip the company) when there is none, and otherwise builds a period around one random match,
so answers have 1-50 IDs after the degenerate-rejection loop in tasks.sample_task.
"""

from pydantic import BaseModel, Field
from scar_schemas.models import AnswerType, TaskFamily

from .base import (
    END_DESC,
    FORBID_EXTRA,
    START_DESC,
    DateStr,
    Money,
    Period,
    Template,
    VendorPeriod,
    anomaly_period,
    check_period,
    check_vendor,
    column,
    id_set_answer,
    money,
    period_params,
    sql_args,
)

ROUND_THRESHOLDS = (100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000, 20000, 25000, 50000, 100000)


class ThresholdPeriod(BaseModel):
    model_config = FORBID_EXTRA
    threshold: Money = Field(description="Minimum amount in dollars, inclusive.")
    start_date: DateStr = Field(description=START_DESC)
    end_date: DateStr = Field(description=END_DESC)


class DuplicatePayments(Template):
    template_id = "anom.duplicate_payments"
    family = TaskFamily.anomaly
    answer_type = AnswerType.id_set
    description = (
        "Find likely duplicate payments: transactions with the same vendor and amount within 3 days of each other"
        " between two dates."
    )
    difficulty = 2
    difficulties = (2,)
    Params = Period
    sql = (
        """SELECT DISTINCT later.txn_id
FROM ledger.transactions earlier
JOIN ledger.transactions later
  ON later.company_id = earlier.company_id
 AND later.vendor_id = earlier.vendor_id
 AND later.amount = earlier.amount
 AND (later.txn_date, later.txn_id) > (earlier.txn_date, earlier.txn_id)
 AND later.txn_date - earlier.txn_date <= 3
WHERE earlier.company_id = %(company_id)s
  AND earlier.txn_date BETWEEN %(start_date)s AND %(end_date)s
  AND later.txn_date BETWEEN %(start_date)s AND %(end_date)s
ORDER BY later.txn_id""",
    )
    # Every "later" transaction over the whole ledger with its closest earlier partner (sampler anchors).
    candidates_sql = """SELECT MAX(earlier.txn_date), later.txn_date
FROM ledger.transactions earlier
JOIN ledger.transactions later
  ON later.company_id = earlier.company_id
 AND later.vendor_id = earlier.vendor_id
 AND later.amount = earlier.amount
 AND (later.txn_date, later.txn_id) > (earlier.txn_date, earlier.txn_id)
 AND later.txn_date - earlier.txn_date <= 3
WHERE earlier.company_id = %(company_id)s
GROUP BY later.txn_id, later.txn_date
ORDER BY later.txn_date, later.txn_id"""
    phrasings = (
        "Find possible duplicate payments from {start_date} to {end_date}: for every pair of transactions with the"
        " same vendor and the same amount dated at most 3 days apart (both inside the period), list the later one."
        " For same-day pairs, the later one is the one with the larger transaction ID.",
        "Between {start_date} and {end_date} inclusive, which transactions repeat an earlier transaction with the"
        " same vendor for the same amount within 3 days? Only count pairs where both fall in the period, and list"
        " just the later transaction of each pair (on the same day, the larger transaction ID is later).",
        "List the likely duplicate bills dated {start_date} through {end_date}: a transaction counts if an earlier"
        " one in the period has the same vendor and amount and is at most 3 days older."
        " Order is by date, then transaction ID.",
        "Check {start_date} to {end_date} for duplicate payments. Return the later transaction of every"
        " same-vendor, same-amount pair that is 0 to 3 days apart with both transactions in the period"
        " (ties on date go by transaction ID).",
        "Which transactions from {start_date} to {end_date} look like duplicates, meaning an earlier transaction no"
        " more than 3 days before, within the same period, has the same vendor and amount? For pairs on the same"
        " date, report the one with the larger transaction ID.",
    )

    def sample(self, conn, company, rng, difficulty):
        anchors = conn.execute(self.candidates_sql, {"company_id": company.company_id}).fetchall()
        if not anchors:
            return None
        earlier, later = rng.choice(anchors)
        return period_params(anomaly_period(rng, company, earlier, later))

    def check(self, conn, company, p):
        check_period(company, p)

    def answer(self, conn, company, p):
        return id_set_answer(column(conn, self.sql[0], sql_args(company, p)))

    def placeholders(self, company, p):
        return {"start_date": p["start_date"], "end_date": p["end_date"]}

    def interpretation(self, company, p):
        return (
            f"The later transaction (by date, then transaction ID) of every pair at {company.name} with the same"
            f" vendor and amount at most 3 days apart, both dated from {p['start_date']} to {p['end_date']}."
        )


class RoundAmountsOver(Template):
    template_id = "anom.round_amounts_over"
    family = TaskFamily.anomaly
    answer_type = AnswerType.id_set
    description = "Find transactions between two dates with round amounts (multiples of 100) at or above a threshold."
    difficulty = 1
    difficulties = (1,)
    Params = ThresholdPeriod
    sql = (
        """SELECT txn_id
FROM ledger.transactions
WHERE company_id = %(company_id)s
  AND txn_date BETWEEN %(start_date)s AND %(end_date)s
  AND amount >= %(threshold)s AND mod(amount, 100) = 0
ORDER BY txn_id""",
    )
    phrasings = (
        "List the transactions from {start_date} to {end_date}, inclusive, with an amount of at least {threshold}"
        " that is an exact multiple of $100.",
        "Which transactions dated {start_date} through {end_date} are round amounts (exact multiples of $100) of"
        " {threshold} or more?",
        "Find every transaction between {start_date} and {end_date} (both included) whose amount is a multiple of"
        " $100 and at least {threshold}.",
        "From {start_date} to {end_date} inclusive, flag all transactions of {threshold} or more whose amounts are"
        " exact multiples of $100.",
        "Identify transactions dated {start_date} to {end_date}, inclusive, with round amounts divisible by $100"
        " that are greater than or equal to {threshold}.",
    )

    def sample(self, conn, company, rng, difficulty):
        anchors = conn.execute(
            "SELECT txn_date, amount FROM ledger.transactions"
            " WHERE company_id = %s AND amount > 0 AND mod(amount, 100) = 0 ORDER BY txn_date, txn_id",
            (company.company_id,),
        ).fetchall()
        if not anchors:
            return None
        txn_date, amount = rng.choice(anchors)
        threshold = rng.choice([t for t in ROUND_THRESHOLDS if t <= amount])
        period = period_params(anomaly_period(rng, company, txn_date, txn_date))
        return period and {"threshold": f"{threshold}.00", **period}

    def check(self, conn, company, p):
        check_period(company, p)

    def answer(self, conn, company, p):
        return id_set_answer(column(conn, self.sql[0], sql_args(company, p)))

    def placeholders(self, company, p):
        return {"threshold": money(p["threshold"]), "start_date": p["start_date"], "end_date": p["end_date"]}

    def interpretation(self, company, p):
        return (
            f"Transactions at {company.name} from {p['start_date']} to {p['end_date']}, dates inclusive, with an"
            f" amount of at least {money(p['threshold'])} that is a multiple of $100."
        )


class VendorOutliers(Template):
    template_id = "anom.vendor_outliers"
    family = TaskFamily.anomaly
    answer_type = AnswerType.id_set
    description = "Find one vendor's transactions between two dates that are unusually large, over 3 times its median."
    difficulty = 3
    difficulties = (3,)
    Params = VendorPeriod
    sql = (
        """WITH v AS (
  SELECT amount, ROW_NUMBER() OVER (ORDER BY amount) AS rn, COUNT(*) OVER () AS n
  FROM ledger.transactions
  WHERE company_id = %(company_id)s AND vendor_id = %(vendor_id)s
), median AS (
  SELECT AVG(amount) AS m FROM v WHERE rn IN ((n + 1) / 2, (n + 2) / 2)
)
SELECT t.txn_id
FROM ledger.transactions t, median
WHERE t.company_id = %(company_id)s AND t.vendor_id = %(vendor_id)s
  AND t.txn_date BETWEEN %(start_date)s AND %(end_date)s
  AND t.amount > 3 * median.m
ORDER BY t.txn_id""",
    )
    # Every outlier of every vendor over the whole ledger (sampler anchors).
    candidates_sql = """WITH v AS (
  SELECT vendor_id, amount,
         ROW_NUMBER() OVER (PARTITION BY vendor_id ORDER BY amount) AS rn,
         COUNT(*) OVER (PARTITION BY vendor_id) AS n
  FROM ledger.transactions
  WHERE company_id = %(company_id)s AND vendor_id IS NOT NULL
), median AS (
  SELECT vendor_id, AVG(amount) AS m FROM v WHERE rn IN ((n + 1) / 2, (n + 2) / 2) GROUP BY vendor_id
)
SELECT t.vendor_id, t.txn_date
FROM ledger.transactions t JOIN median ON median.vendor_id = t.vendor_id
WHERE t.company_id = %(company_id)s AND t.amount > 3 * median.m
ORDER BY t.txn_date, t.txn_id"""
    phrasings = (
        "Which {vendor} transactions from {start_date} to {end_date}, inclusive, were more than 3 times the vendor's"
        " median transaction amount? Use the median over all of {vendor}'s transactions in the ledger.",
        "Find {vendor} transactions dated {start_date} through {end_date} whose amount is over 3x the median of all"
        " {vendor} transactions (any date).",
        "List the outlier transactions with {vendor} between {start_date} and {end_date} (both included): amounts"
        " greater than three times {vendor}'s median amount across the whole ledger.",
        "From {start_date} to {end_date} inclusive, which charges from {vendor} exceeded 3 times that vendor's"
        " overall median transaction amount?",
        "Flag every {vendor} transaction dated {start_date} to {end_date}, inclusive, that is larger than 3 times"
        " the median amount of all transactions with {vendor}.",
    )

    def sample(self, conn, company, rng, difficulty):
        anchors = conn.execute(self.candidates_sql, {"company_id": company.company_id}).fetchall()
        if not anchors:
            return None
        vendor, txn_date = rng.choice(anchors)
        period = period_params(anomaly_period(rng, company, txn_date, txn_date))
        return period and {"vendor_id": vendor, **period}

    def check(self, conn, company, p):
        check_vendor(company, p)
        check_period(company, p)

    def answer(self, conn, company, p):
        return id_set_answer(column(conn, self.sql[0], sql_args(company, p)))

    def placeholders(self, company, p):
        return {"vendor": company.vendors[p["vendor_id"]], "start_date": p["start_date"], "end_date": p["end_date"]}

    def interpretation(self, company, p):
        vendor = company.vendors[p["vendor_id"]]
        return (
            f"Transactions at {company.name} with {vendor} from {p['start_date']} to {p['end_date']}, dates"
            f" inclusive, whose amount exceeds 3 times {vendor}'s median transaction amount over the whole ledger."
        )
