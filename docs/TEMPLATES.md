# Template catalog (P1-06)

This is the final template catalog of SPEC 9.5, as implemented in `env/scar_env/templates/`.
Every expected answer is computed by code, with the SQL quoted below, over `ledger.*` (R5).
A test (`env/tests/test_templates.py`) fails if any SQL in the code is not quoted here verbatim.

## Summary

| template_id | family | answer_type | base difficulty | supported difficulties | tolerance |
|---|---|---|---:|---|---|
| `agg.total_by_account_period` | aggregation | number | 1 | 1, 2, 3 (period length) | 0.01 |
| `agg.total_by_vendor_period` | aggregation | number | 1 | 1, 2, 3 (period length) | 0.01 |
| `agg.count_by_account_period` | aggregation | number | 1 | 1, 2, 3 (period length) | 0 |
| `agg.top_vendor_by_spend` | aggregation | id_set | 2 | 2, 3 (period length) | exact set |
| `agg.mom_change_account` | aggregation | number | 2 | 2 | 0.01 |
| `agg.share_of_expense` | aggregation | number | 3 | 3 | 0.05 |
| `anom.duplicate_payments` | anomaly | id_set | 2 | 2 | exact set |
| `anom.round_amounts_over` | anomaly | id_set | 1 | 1 | exact set |
| `anom.vendor_outliers` | anomaly | id_set | 3 | 3 | exact set |
| `book.accrual` | bookkeeping | journal_entry | 1 | 1 | 0.01 per line (verifier) |
| `book.prepaid_amortization` | bookkeeping | journal_entry | 2 | 2 | 0.01 per line (verifier) |
| `book.reclassify` | bookkeeping | journal_entry | 3 | 3 | 0.01 per line (verifier) |

`GET /templates` returns the twelve `TemplateSpec`s in this order.

## Shared rules

### Scope, dates and money

- Every query is filtered by the task's `company_id`, so IDs, totals and medians never mix companies.
- Periods are inclusive on both ends (`txn_date BETWEEN start_date AND end_date`).
- Dates are `YYYY-MM-DD` strings and months are `YYYY-MM` strings in params.
- A company's data range is the first to the last `txn_date` of its transactions.
- Money params are stored as strings with exactly 2 decimals (`500` becomes `"500.00"`); more than 2 decimals is rejected.
- Number answers are the exact `NUMERIC` value from SQL; the verifier compares with the tolerance in the table.

### Validation (used by `make_task` and `POST /tasks/from_params`)

- Params must match the template's JSON Schema (`params_schema`, generated from a pydantic model): all fields required, no extra fields, right types and formats.
- Every account, vendor and transaction ID must belong to the task's company.
- Dates must be real calendar dates, and `start_date` must not be after `end_date`.
- A query period must overlap the company's data range, so "all of 2026" is accepted for a company whose data ends in October 2026.
- Bookkeeping dates (`entry_date`, `start_month`) may fall outside the data range, because entries can be booked for any date.
- Account-type rules are listed per template (for example, `share_of_expense` needs an expense account).
- An answer that is undefined (a share of a zero expense total) is rejected with an error.
- Errors are short and name the field, for example `start_date: 2026-02-30 is not a valid date`.

### Prompts

- Phrasings show account and vendor names, ISO dates, month names ("March 2026") and amounts ("$1,234.56"), never raw account or vendor IDs; the agent must look IDs up with tools.
- The one exception is `book.reclassify`, which shows the `txn_id` (see that template).
- Every phrasing states every param, so each one is a complete question.
- The company name is not in the prompt; the agent's user message already starts with `Company: {name}` (SPEC 9.2).

### Degenerate rejection (SPEC 9.5)

- `tasks.sample_task` samples params, computes the answer, and resamples when the answer is degenerate, up to 20 tries; then it gives up on that company (returns None).
- Degenerate means: zero matching transactions for number templates, an empty or over-50 `id_set`, or the template-specific rule listed below.
- A sampler returns None at once when the company has nothing to sample (for example, no vendor outliers at all); that company is skipped.
- Bookkeeping answers are never degenerate.
- `from_params` does not apply the degenerate rule: a judge may ask about a period with no transactions, and the answer is 0 or an empty set.

### Difficulty

- `TemplateSpec.difficulty` is the base difficulty from SPEC 9.5.
- `Template.difficulties` lists every difficulty the sampler can honor; it always starts at the base and never exceeds 3.
- The mutator (SPEC 9.6) should use difficulty + 1 only when it is in `difficulties`.
- Period-based aggregation templates honor difficulty through the period length, anchored on a real transaction:

| difficulty | period length | extra rule |
|---:|---|---|
| 1 | 7 to 31 days | inside one calendar month (shorter only where the data range clips that month) |
| 2 | 32 to 92 days | always crosses at least one month boundary |
| 3 | 93 to 366 days | clipped to the data range |

- Longer periods mean more rows than one `get_transactions` call returns (100 by default), so the agent has to page or aggregate in SQL.
- The other templates support only their base difficulty; they have no knob that keeps the definition unchanged.
- Judge tasks from `from_params` get the template's base difficulty.

### Generation (`POST /tasks/generate`)

- `origin=generated` requires `split=train` and writes positions 0..n-1 of `app.train_pool` in the returned order; it returns 409 if the pool already has rows.
- `origin=eval` requires `split=eval`, no `family`, and `n` divisible by 3; it stores `EvalSet` `eval_v1` with the sorted task IDs and the sha256 of `"\n".join(sorted ids)`, and returns 409 if `eval_v1` exists.
- Other origins, split/origin mismatches and `n < 1` return 422.
- Families take turns (aggregation, anomaly, bookkeeping), or only `family` when given; `n % 3` extra tasks go to the first families.
- Within a family, slot k has difficulty `1 + k % 3`, so difficulties are spread evenly (within one task).
- Slot k uses the family's templates that support its difficulty in turn, starting with number `(k // 3) % count` in catalog order.
- Companies of the split are spread evenly: each company appears `n_family / n_companies` times (plus or minus one), in an order shuffled with `random.Random(f"{seed}:{family}:companies")`.
- Each slot samples with `random.Random(f"{seed}:{family}:{k}")`.
- If the slot's company cannot support the template, the slot moves to the next company in `company_id` order, then to the next template; if nothing works the request fails with 422 `cannot_generate`.
- The returned list interleaves the families slot by slot; the same request on the same ledger gives the same params and prompts.
- Under this rule each anomaly and bookkeeping difficulty maps to one template, so `eval_v1` has about 33 tasks of each of those six templates.

### Judge tasks (`POST /tasks/from_params`)

- `origin` must be `judge` (422 otherwise).
- Unknown templates, unknown or eval-split companies and invalid params give `valid=false` with an error and an empty `interpretation`.
- `dry_run=true` validates, computes the answer (so undefined answers are caught) and returns the interpretation without storing anything.
- Otherwise the task is stored with the judge's `prompt`, or with a phrasing picked by `random.Random` seeded from the request.
- The interpretation restates the params by name and never includes the expected number, IDs, entry amounts or ledger-derived dates.

### Internal API

- `scar_env.templates`: `TEMPLATES`, `get_template(template_id)`, `load_company(conn, company_id)`, and per template `sample`, `validate`, `answer`, `render`, `interpretation` (module docstring).
- `scar_env.tasks`: `make_task(conn, template_id, company_id, params, *, origin, difficulty, rng, prompt=None, parent_failure_id=None) -> Task`, `sample_task(conn, template_id, company_id, *, rng, difficulty, origin, parent_failure_id=None) -> Task | None`, `generate_tasks(...)`, `eval_set(task_ids)`, `interpretation(conn, task)`.
- Neither function stores anything; callers use `scar_env.store.store_task`.
- With the same inputs and rng state, only `task_id` and `created_at` differ between runs.

## Aggregation templates

Samplers pick only expense accounts that have transactions, because sums on asset and liability accounts mean "amount billed" rather than spend (`docs/DATASET.md`).
Validation still accepts any account of the company for the sum and count templates.

### `agg.total_by_account_period`

Params: `account_id`, `start_date`, `end_date`.
Answer: the net sum of the account's transactions in the period; the second column is the row count used for the degenerate rule.

```sql
SELECT COALESCE(SUM(amount), 0), COUNT(*)
FROM ledger.transactions
WHERE company_id = %(company_id)s AND account_id = %(account_id)s
  AND txn_date BETWEEN %(start_date)s AND %(end_date)s
```

- Negative rows (manager-wage reclassifications) are included, so the sum is net, which matches the P&L.
- Degenerate: no transactions in the period.
- Sampler: a random expense account with transactions, a random one of its transaction dates as anchor, and a period around it sized by difficulty.
- Example: "What is the total amount recorded in the Advertising and Marketing account from 2026-10-01 to 2026-10-06, inclusive?"

### `agg.total_by_vendor_period`

Params: `vendor_id`, `start_date`, `end_date`.
Answer: the sum of the vendor's transactions in the period (only bill rows carry a vendor).

```sql
SELECT COALESCE(SUM(amount), 0), COUNT(*)
FROM ledger.transactions
WHERE company_id = %(company_id)s AND vendor_id = %(vendor_id)s
  AND txn_date BETWEEN %(start_date)s AND %(end_date)s
```

- Degenerate: no transactions in the period.
- Sampler: a random vendor with transactions, anchored on one of its transaction dates, period sized by difficulty.

### `agg.count_by_account_period`

Params: `account_id`, `start_date`, `end_date`.
Answer: the number of transactions; tolerance 0.

```sql
SELECT COUNT(*)
FROM ledger.transactions
WHERE company_id = %(company_id)s AND account_id = %(account_id)s
  AND txn_date BETWEEN %(start_date)s AND %(end_date)s
```

- Every row counts, including negative ones.
- Degenerate: a count of 0.
- Sampler: as `agg.total_by_account_period`.

### `agg.top_vendor_by_spend`

Params: `start_date`, `end_date`.
Answer: the vendor ID or IDs with the highest total in the period, including every vendor tied for first.

```sql
WITH spend AS (
  SELECT vendor_id, SUM(amount) AS total
  FROM ledger.transactions
  WHERE company_id = %(company_id)s AND vendor_id IS NOT NULL
    AND txn_date BETWEEN %(start_date)s AND %(end_date)s
  GROUP BY vendor_id
)
SELECT vendor_id FROM spend WHERE total = (SELECT MAX(total) FROM spend) ORDER BY vendor_id
```

- "Spend" means the billed amount, since only bill rows have vendors.
- Ties are exact `NUMERIC` equality.
- Degenerate: no vendor transactions in the period (empty set).
- Sampler: anchored on a random vendor transaction date, period sized by difficulty 2 or 3.

### `agg.mom_change_account`

Params: `account_id`, `month` (`YYYY-MM`).
Answer: the account's total in `month` minus its total in the calendar month before; positive means an increase.

```sql
SELECT COALESCE(SUM(amount) FILTER (WHERE to_char(txn_date, 'YYYY-MM') = %(month)s), 0)
     - COALESCE(SUM(amount) FILTER (WHERE to_char(txn_date, 'YYYY-MM') = %(prev_month)s), 0),
       COUNT(*) FILTER (WHERE to_char(txn_date, 'YYYY-MM') = %(month)s),
       COUNT(*) FILTER (WHERE to_char(txn_date, 'YYYY-MM') = %(prev_month)s)
FROM ledger.transactions
WHERE company_id = %(company_id)s AND account_id = %(account_id)s
```

- `prev_month` is computed in Python (`2026-01` gives `2025-12`).
- Degenerate: the month or the month before has no transactions in the account.
- Sampler: a random expense account with transactions and a random month such that both the month and the month before lie entirely inside the data range, so it never picks a company's first (partial) month.
- Validation is looser for judges: the month must start on or before the last data date and must not be the company's first month.

### `agg.share_of_expense`

Params: `account_id` (must be an expense account), `start_date`, `end_date`.
Answer: 100 times the account's total divided by the total of all expense-account transactions in the period, rounded to 2 decimals; tolerance 0.05.

```sql
SELECT ROUND(100 * COALESCE(SUM(t.amount) FILTER (WHERE t.account_id = %(account_id)s), 0)
                   / NULLIF(SUM(t.amount), 0), 2),
       COUNT(*) FILTER (WHERE t.account_id = %(account_id)s),
       SUM(t.amount)
FROM ledger.transactions t
JOIN ledger.accounts a ON a.account_id = t.account_id
WHERE t.company_id = %(company_id)s AND a.account_type = 'expense'
  AND t.txn_date BETWEEN %(start_date)s AND %(end_date)s
```

- Rounding is Postgres `ROUND(numeric, 2)`: half away from zero.
- The denominator includes COGS, wages, depreciation and interest from journal lines, and it is net of negative rows.
- If the denominator is zero or negative the share is undefined: `from_params` rejects it and the sampler resamples.
- Degenerate: the account has no transactions in the period.
- Sampler: as `agg.total_by_account_period`, with difficulty 3 periods (93 to 366 days).

## Anomaly templates

The SPEC 9.5 definitions are kept as they are (`docs/DECISIONS.md`); answers count natural and planted matches alike.
Each sampler first lists every match over the company's whole data range; if there is none it skips the company.
Otherwise it builds a period around one random match, 7 to M days long, where M is drawn from (14, 31, 92, 183, 366) so that dense companies still get short periods.
The degenerate loop then keeps answers between 1 and 50 IDs.
Prompts give the full rule, including the tie-break, so the question has one correct answer.

### `anom.duplicate_payments`

Params: `start_date`, `end_date`.
Answer: the `txn_id` of the later transaction in every pair with the same vendor and the same amount dated at most 3 days apart, both inside the period.

```sql
SELECT DISTINCT later.txn_id
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
ORDER BY later.txn_id
```

- "Later" is ledger order: by date, then by `txn_id`; for two transactions on the same day, the larger `txn_id` is later.
- Both transactions of a pair must be inside the period; a transaction whose only earlier partner is before `start_date` does not count.
- In a chain (A, B, C each within 3 days) every transaction except the first is listed once.
- Rows without a vendor (journal lines) never match.
- The same account is not required, only the same vendor and amount.
- Degenerate: an empty set or more than 50 IDs.
- Sampler anchors (one row per "later" transaction, with its closest earlier partner, so the period contains the whole pair):

```sql
SELECT MAX(earlier.txn_date), later.txn_date
FROM ledger.transactions earlier
JOIN ledger.transactions later
  ON later.company_id = earlier.company_id
 AND later.vendor_id = earlier.vendor_id
 AND later.amount = earlier.amount
 AND (later.txn_date, later.txn_id) > (earlier.txn_date, earlier.txn_id)
 AND later.txn_date - earlier.txn_date <= 3
WHERE earlier.company_id = %(company_id)s
GROUP BY later.txn_id, later.txn_date
ORDER BY later.txn_date, later.txn_id
```

### `anom.round_amounts_over`

Params: `threshold` (money, greater than 0), `start_date`, `end_date`.
Answer: the `txn_id`s in the period whose amount is at least the threshold and an exact multiple of 100.

```sql
SELECT txn_id
FROM ledger.transactions
WHERE company_id = %(company_id)s
  AND txn_date BETWEEN %(start_date)s AND %(end_date)s
  AND amount >= %(threshold)s AND mod(amount, 100) = 0
ORDER BY txn_id
```

- The threshold is inclusive (`>=`), and prompts say "at least".
- Negative amounts never match because the threshold is positive.
- Degenerate: an empty set or more than 50 IDs.
- Sampler: anchored on a random positive multiple-of-100 transaction; the threshold is a random value from (100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000, 20000, 25000, 50000, 100000) that does not exceed the anchor's amount.

### `anom.vendor_outliers`

Params: `vendor_id`, `start_date`, `end_date`.
Answer: the vendor's `txn_id`s in the period whose amount is more than 3 times the vendor's median amount over the whole ledger.

```sql
WITH v AS (
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
ORDER BY t.txn_id
```

- The median is the conventional one: the middle amount, or the mean of the two middle amounts for an even count.
- It is computed exactly in `NUMERIC` (not `percentile_cont`, which works in floating point).
- It covers all of the vendor's transactions at any date, not only the period.
- The comparison is strict: an amount of exactly 3 times the median is not an outlier.
- Degenerate: an empty set or more than 50 IDs.
- Sampler anchors (every outlier of every vendor; the vendor of the chosen anchor becomes `vendor_id`):

```sql
WITH v AS (
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
ORDER BY t.txn_date, t.txn_id
```

## Bookkeeping templates

Answers are journal entries, compared by the verifier as a multiset of `(entry_date, sorted lines)` with amounts within 0.01 and memos ignored (SPEC 9.3).
Each entry has two lines, a debit and a credit of the same amount.
Accrual and prepaid entries follow directly from the params, whose accounts are checked against `ledger.accounts`; reclassify reads the transaction from `ledger.transactions`.

### `book.accrual`

Params: `expense_account_id` (expense), `liability_account_id` (liability), `amount` (money, greater than 0), `entry_date`.
Answer: one entry on `entry_date` that debits the expense account and credits the liability account for `amount`.

- Sampler: a random month-end inside the data range and an amount from $50.00 to $5,000.00.
- The sampler pairs the expense with the liability an accountant would use, by account name (the names are the same in every company): Wage Expense, Project Manager Wages and Wages go to Wages Payable, Interest Expense to Accrued Interest, Income Tax Expense to Income Tax Payable, and every other expense to Accounts Payable.
- It never accrues Cost of Goods Sold, Depreciation or Bad Debts, which are booked by other entries.
- Validation accepts any expense and liability pair, since the judge states both accounts.

### `book.prepaid_amortization`

Params: `prepaid_account_id` (asset), `expense_account_id` (expense), `total` (money), `months` (2 to 24), `start_month` (`YYYY-MM`).
Answer: `months` entries, one on the last day of each month from `start_month` on, each debiting the expense account and crediting the prepaid account.

- Each month's amount is `total / months` rounded half up to cents; the last month gets `total` minus the other months, so the entries sum to `total` exactly.
- Example: $1,000.00 over 3 months from December 2025 gives 333.33 on 2025-12-31, 333.33 on 2026-01-31 and 333.34 on 2026-02-28.
- The last day of the month follows the calendar, including February 29 in leap years.
- `total` must be large enough that every month gets a positive amount.
- Sampler: always the loader's `{company_id}:acct-prepaid-expenses` account, a random expense account, a total from $600.00 to $24,000.00, `months` from (2, 3, 4, 6, 12), and a start month such that every entry date lies inside the data range.

### `book.reclassify`

Params: `txn_id`, `to_account_id`.
Answer: one entry on the transaction's date that debits `to_account_id` and credits the transaction's current account for the transaction's amount.

```sql
SELECT txn_date, amount, account_id
FROM ledger.transactions
WHERE company_id = %(company_id)s AND txn_id = %(txn_id)s
```

- Prompts show the `txn_id`: a transaction has no human name, and vendor plus date plus amount is not unique in this ledger (recurring identical bills, planted duplicates).
- The agent still has to look up the transaction's date, amount and current account (with `sql_query`).
- Validation: the transaction must belong to the company and have a positive amount, and `to_account_id` must be a different account of the company.
- Sampler: a random positive-amount bill row and a random expense account other than its current one.

## Checked on the real ledger

On 2026-10-17 each template was sampled read-only against the loaded real ledger (100 companies, nothing stored), cycling through the 70 train companies and the supported difficulties until 50 tasks were built.

- All 12 templates produced 50 valid, non-degenerate tasks, and every one of the 600 answers matched an independent Python computation over the raw rows.
- Mean tries per task were 1.0 for most templates, 1.28 for `agg.mom_change_account` (at most 3) and 1.5 for `anom.duplicate_payments` (at most 6); no company ever used up its 20 tries.
- Answer sizes: duplicate payments 1 to 45 IDs, round amounts 1 to 48, vendor outliers 1 to 8, top vendor always 1.

Companies each sampler skips (a scan of all 100 companies; every other template supports all of them):

- `anom.round_amounts_over` (no positive multiple-of-100 amount): train company-064, company-065, company-066, company-067, company-068, company-073, company-075, company-076, company-097, company-098; eval company-074, company-077.
- `anom.vendor_outliers` (no vendor outlier): 37 of 70 train companies (company-010, company-012, company-013, company-016, company-035, company-036, company-057, company-058, company-059, company-061, company-063 to company-073, company-075, company-076, company-078, company-081, company-086 to company-093, company-096 to company-099) and 14 of 30 eval companies (company-022, company-026, company-049, company-050, company-062, company-074, company-077, company-079, company-080, company-082, company-083, company-094, company-095, company-100).
- So the vendor-outlier share of `eval_v1` (about 33 tasks) comes from the 16 eval companies that have outliers.
