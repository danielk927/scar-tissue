# Decisions

Append-only. One line per decision, under your person's heading (SPEC R11).

## Person 1

- 2026-10-17: `ledger.transactions` = bill lines + journal lines on expense-classified accounts only (no income, payments, transfers, bank feed); reconciles to each company's P&L. Mapping and sign rule in `docs/DATASET.md`.
- 2026-10-17: Canonical IDs: `company_id` = raw `company-NNN`; account/vendor IDs = `{company_id}:{raw id}`; `txn_id` = `{company_id}:{raw bill or JE id}:{line_num}`.
- 2026-10-17: The loader adds one asset account "Prepaid Expenses" (`{company_id}:acct-prepaid-expenses`) to every company so `book.prepaid_amortization` works; disclosed in `docs/DATASET_CHANGES.md`.
- 2026-10-17: Anomaly templates keep the Section 9.5 SQL definitions; the sampler only picks companies, vendors and windows with 1-50 matches. Only duplicate payments are planted (per 9.5); no planted round amounts or vendor outliers.
- 2026-10-17: Planted duplicates: originals are bills dated at least 3 days before the company's last transaction; per-company RNG `random.Random(f"plant:{company_id}")` draws the count (5-15), then originals from bills ordered by txn_id, then one 1-3 day shift per copy.
- 2026-10-17: Ledger checksums for idempotency = md5 over each table's rows ordered by primary key with `COLLATE "C"`.
- 2026-10-17: `sql_query` sets `app.company_id` BEFORE `SET LOCAL ROLE`, and the env service revokes `set_config` from PUBLIC at startup (`harden_sql_tool`); without this a query could read other companies or regain superuser. Proposed for `ddl.sql` via SPEC 8.9.
- 2026-10-17: The four list tools return `{"columns", "has_more", "row_count", "rows"}` (column-oriented, sorted keys so the flags survive the agent's 6000-char cut); `create_journal_entry` returns `{"entry_id": "entry_<hex>"}` (`new_id("entry")`).
- 2026-10-17: Tool calls run with a 10 s statement timeout (503 `tool_timeout`); DB failures are 503 `db_unavailable`; unknown account/vendor filters return no rows; null optional args count as absent; numeric args sent as strings are rejected.
- 2026-10-17: The verifier counts a repeated entry_id once and matches journal entries one-to-one (not greedily).
- 2026-10-17: The env service is served from `scar_env.main:app` (Sentry init lives there, so tests never send events).

## Person 2

## Person 3
