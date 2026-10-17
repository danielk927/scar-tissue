# Decisions

Append-only. One line per decision, under your person's heading (SPEC R11).

## Person 1

- 2026-10-17: `ledger.transactions` = bill lines + journal lines on expense-classified accounts only (no income, payments, transfers, bank feed); reconciles to each company's P&L. Mapping and sign rule in `docs/DATASET.md`.
- 2026-10-17: Canonical IDs: `company_id` = raw `company-NNN`; account/vendor IDs = `{company_id}:{raw id}`; `txn_id` = `{company_id}:{raw bill or JE id}:{line_num}`.
- 2026-10-17: The loader adds one asset account "Prepaid Expenses" (`{company_id}:acct-prepaid-expenses`) to every company so `book.prepaid_amortization` works; disclosed in `docs/DATASET_CHANGES.md`.
- 2026-10-17: Anomaly templates keep the Section 9.5 SQL definitions; the sampler only picks companies, vendors and windows with 1-50 matches. Only duplicate payments are planted (per 9.5); no planted round amounts or vendor outliers.

## Person 2

## Person 3
