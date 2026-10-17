# Dataset changes

The QuickBooks challenge asks for clear disclosure of how we changed the dataset.
This file lists every difference between the raw files and our canonical ledger (`ledger.*`, SPEC Section 8.7), and why.

- Source: Hugging Face dataset `CL251215/QuickBooks-Hackathon-2026`, revision `dc68d478a190c879f26fd94b764298c2c6308fa7`, file `hackathon-100-participant.tar.gz` (100 companies).
- Loader: `env/scar_env/load_ledger.py`, run as `uv run python -m scar_env.load_ledger --data-dir <hackathon-100 dir>`.
  It truncates and reloads all five ledger tables in one transaction.
- The raw structure and the exact field mapping are documented in `docs/DATASET.md`.
- We never edit the raw files.
  Every change below happens in the loader and is deterministic: the same files always give the same ledger and the same table checksums.

## 1. Data we did not load

The canonical ledger holds money spent, on an accrual basis: one row per bill line and one row per journal-entry line on an expense account.
With that scope, each company's expense accounts reconcile to the cent with the non-income lines of its own `reports/profit_and_loss.yaml` (tested for all 100 companies).
Everything else is left out.

| raw data | raw rows | why not loaded |
|---|---:|---|
| `invoices.yaml`, `sales_receipts.yaml` | 84,451 and 28,501 | Revenue, not money spent; no task template uses it. |
| `customer_payments.yaml` | 143,616 | Cash collected against receivables. |
| `bill_payments.yaml` | 36,032 | Cash settlement of bills that are already loaded. Loading them would double-count vendor spend and, because each payment repeats its bill's vendor and amount a few days later, raise duplicate-payment matches from 11,025 to 34,517. |
| journal-entry lines on non-expense accounts | 183,240 | The balance-sheet side of every loaded line, plus entries with no expense line at all (wage payments, card settlements, financing, owner distributions, deposits, opening equity). |
| `bank_transactions.yaml` | 234,610 | A derived bank feed that mirrors the documents above exactly. |
| `payroll_runs.yaml` | 25,733 | Derived; each run mirrors one wage-payment journal entry. |
| `customers`, `employees`, `projects`, `estimates`, `terms`, `manifest`, `company.yaml`, the balance sheet and aging reports | - | Not part of the canonical schema. |

`items.yaml` is read only to find the purchase account of product-bill lines; `index.json` supplies each company's name and industry.

Fields of loaded rows that we do not carry over:

- Bill `memo`: it comes from 20 fixed strings that are often unrelated to the bill, so the bill line's description is used instead.
- Journal-line descriptions: they are generic (for example "Wages earned."), so the entry's `memo` is used instead.
- The raw `account_type` and `account_subtype` of accounts: canonical `account_type` comes from `classification`, with `revenue` renamed to `income`.
- Contact details, payment terms, document numbers, due dates, project IDs, item quantities and the simulator's provenance fields.

## 2. Representation changes (values unchanged)

- IDs are prefixed with the company, because raw IDs repeat across companies: `{company_id}:{raw id}` for accounts and vendors, `{company_id}:{raw bill or entry id}:{line_num}` for transactions.
- Journal-entry amounts become one signed number, debit minus credit.
  A credit to an expense account is therefore negative; this affects 3,630 manager-wage reclassification lines and keeps account totals equal to the P&L.

## 3. Added account: Prepaid Expenses

- Every company gets one extra asset account named "Prepaid Expenses", with ID `{company_id}:acct-prepaid-expenses` (100 accounts in total).
- Why: no company in the raw data has a prepaid account, and the `book.prepaid_amortization` task template needs one.
- The loader posts no transactions to it; it only exists as a target for journal entries the agent writes in its sandbox.

## 4. Planted duplicate bills

To guarantee that duplicate-payment tasks have answers in every company (SPEC Section 9.5), the loader adds copies of real bills.

- For each company, a random number generator seeded with the string `plant:{company_id}` draws a count between 5 and 15.
- The originals are drawn with the same generator from that company's bill rows, ordered by `txn_id`, that are dated at least 3 days before the company's last transaction.
  Only bill rows qualify, because only bills have a vendor.
- Each copy keeps the original's account, vendor, amount, description and `txn_type` (`bill`).
  It gets the new ID `{company_id}:planted-dup-NNNN:1` and is dated 1 to 3 days after the original (also drawn from the generator), so it never falls after the company's last transaction.
- Each copy is recorded in `ledger.planted_anomalies` with `anomaly_id` `{company_id}:planted-dup-NNNN`, kind `duplicate_payment`, `txn_ids` `[original, copy]` and `details` `{original_txn_id, days_shifted}`.
  The agent's SQL tool cannot read that table.
- The copies are ordinary rows in `ledger.transactions`, so account and vendor totals include them and differ from the raw P&L by the planted amounts.
- Task answers are still computed by the SQL definitions over the whole ledger, so natural duplicates in the raw data count as well.

Per-company counts and the full list of planted rows, generated from the final loader run (`--changes-doc docs/DATASET_CHANGES.md`):

<!-- BEGIN GENERATED: python -m scar_env.load_ledger --changes-doc; do not edit by hand -->

989 planted rows in 100 companies.

| company_id | planted rows |
|---|---:|
| company-001 | 10 |
| company-002 | 10 |
| company-003 | 15 |
| company-004 | 5 |
| company-005 | 13 |
| company-006 | 11 |
| company-007 | 13 |
| company-008 | 13 |
| company-009 | 6 |
| company-010 | 7 |
| company-011 | 14 |
| company-012 | 7 |
| company-013 | 6 |
| company-014 | 9 |
| company-015 | 10 |
| company-016 | 10 |
| company-017 | 13 |
| company-018 | 12 |
| company-019 | 7 |
| company-020 | 13 |
| company-021 | 10 |
| company-022 | 13 |
| company-023 | 11 |
| company-024 | 9 |
| company-025 | 11 |
| company-026 | 5 |
| company-027 | 15 |
| company-028 | 6 |
| company-029 | 7 |
| company-030 | 12 |
| company-031 | 13 |
| company-032 | 7 |
| company-033 | 14 |
| company-034 | 15 |
| company-035 | 10 |
| company-036 | 8 |
| company-037 | 10 |
| company-038 | 15 |
| company-039 | 11 |
| company-040 | 14 |
| company-041 | 10 |
| company-042 | 13 |
| company-043 | 8 |
| company-044 | 11 |
| company-045 | 12 |
| company-046 | 6 |
| company-047 | 6 |
| company-048 | 11 |
| company-049 | 15 |
| company-050 | 6 |
| company-051 | 6 |
| company-052 | 6 |
| company-053 | 9 |
| company-054 | 8 |
| company-055 | 10 |
| company-056 | 9 |
| company-057 | 6 |
| company-058 | 7 |
| company-059 | 7 |
| company-060 | 7 |
| company-061 | 6 |
| company-062 | 14 |
| company-063 | 14 |
| company-064 | 13 |
| company-065 | 11 |
| company-066 | 11 |
| company-067 | 10 |
| company-068 | 6 |
| company-069 | 11 |
| company-070 | 13 |
| company-071 | 6 |
| company-072 | 7 |
| company-073 | 13 |
| company-074 | 7 |
| company-075 | 14 |
| company-076 | 11 |
| company-077 | 8 |
| company-078 | 5 |
| company-079 | 11 |
| company-080 | 13 |
| company-081 | 7 |
| company-082 | 9 |
| company-083 | 13 |
| company-084 | 6 |
| company-085 | 10 |
| company-086 | 8 |
| company-087 | 6 |
| company-088 | 14 |
| company-089 | 5 |
| company-090 | 11 |
| company-091 | 6 |
| company-092 | 11 |
| company-093 | 14 |
| company-094 | 6 |
| company-095 | 13 |
| company-096 | 15 |
| company-097 | 8 |
| company-098 | 13 |
| company-099 | 10 |
| company-100 | 7 |

| planted txn_id | original txn_id | date shift (days) |
|---|---|---:|
| `company-001:planted-dup-0001:1` | `company-001:bill-00122:1` | +1 |
| `company-001:planted-dup-0002:1` | `company-001:bill-00653:1` | +2 |
| `company-001:planted-dup-0003:1` | `company-001:bill-00904:1` | +3 |
| `company-001:planted-dup-0004:1` | `company-001:bill-00905:1` | +2 |
| `company-001:planted-dup-0005:1` | `company-001:bill-01384:1` | +1 |
| `company-001:planted-dup-0006:1` | `company-001:bill-01717:1` | +1 |
| `company-001:planted-dup-0007:1` | `company-001:bill-01750:1` | +3 |
| `company-001:planted-dup-0008:1` | `company-001:bill-utilities-00233:1` | +3 |
| `company-001:planted-dup-0009:1` | `company-001:bill-utilities-00963:1` | +1 |
| `company-001:planted-dup-0010:1` | `company-001:bill-utilities-01734:1` | +2 |
| `company-002:planted-dup-0001:1` | `company-002:bill-02834:1` | +3 |
| `company-002:planted-dup-0002:1` | `company-002:bill-operating-00655:1` | +2 |
| `company-002:planted-dup-0003:1` | `company-002:bill-operating-00841:1` | +2 |
| `company-002:planted-dup-0004:1` | `company-002:bill-operating-01815:1` | +2 |
| `company-002:planted-dup-0005:1` | `company-002:bill-utilities-00138:1` | +3 |
| `company-002:planted-dup-0006:1` | `company-002:bill-utilities-00499:1` | +3 |
| `company-002:planted-dup-0007:1` | `company-002:bill-utilities-02594:1` | +2 |
| `company-002:planted-dup-0008:1` | `company-002:bill-utilities-02902:1` | +3 |
| `company-002:planted-dup-0009:1` | `company-002:bill-variable-services-00140:1` | +3 |
| `company-002:planted-dup-0010:1` | `company-002:bill-variable-services-02904:1` | +2 |
| `company-003:planted-dup-0001:1` | `company-003:bill-01047:1` | +1 |
| `company-003:planted-dup-0002:1` | `company-003:bill-01543:1` | +3 |
| `company-003:planted-dup-0003:1` | `company-003:bill-02736:1` | +2 |
| `company-003:planted-dup-0004:1` | `company-003:bill-04235:1` | +2 |
| `company-003:planted-dup-0005:1` | `company-003:bill-05706:1` | +2 |
| `company-003:planted-dup-0006:1` | `company-003:bill-income-tax-02711:1` | +3 |
| `company-003:planted-dup-0007:1` | `company-003:bill-utilities-00048:1` | +3 |
| `company-003:planted-dup-0008:1` | `company-003:bill-utilities-00049:1` | +3 |
| `company-003:planted-dup-0009:1` | `company-003:bill-utilities-01423:1` | +2 |
| `company-003:planted-dup-0010:1` | `company-003:bill-utilities-03035:1` | +2 |
| `company-003:planted-dup-0011:1` | `company-003:bill-utilities-03409:1` | +1 |
| `company-003:planted-dup-0012:1` | `company-003:bill-utilities-03655:1` | +2 |
| `company-003:planted-dup-0013:1` | `company-003:bill-utilities-04385:1` | +2 |
| `company-003:planted-dup-0014:1` | `company-003:bill-variable-services-04515:1` | +3 |
| `company-003:planted-dup-0015:1` | `company-003:bill-variable-services-04639:1` | +3 |
| `company-004:planted-dup-0001:1` | `company-004:bill-00734:1` | +3 |
| `company-004:planted-dup-0002:1` | `company-004:bill-01048:1` | +1 |
| `company-004:planted-dup-0003:1` | `company-004:bill-01407:1` | +3 |
| `company-004:planted-dup-0004:1` | `company-004:bill-02069:1` | +3 |
| `company-004:planted-dup-0005:1` | `company-004:bill-02082:1` | +2 |
| `company-005:planted-dup-0001:1` | `company-005:bill-01376:1` | +1 |
| `company-005:planted-dup-0002:1` | `company-005:bill-02241:1` | +2 |
| `company-005:planted-dup-0003:1` | `company-005:bill-02242:1` | +1 |
| `company-005:planted-dup-0004:1` | `company-005:bill-02639:1` | +2 |
| `company-005:planted-dup-0005:1` | `company-005:bill-operating-01348:1` | +3 |
| `company-005:planted-dup-0006:1` | `company-005:bill-utilities-01720:1` | +3 |
| `company-005:planted-dup-0007:1` | `company-005:bill-utilities-01876:1` | +1 |
| `company-005:planted-dup-0008:1` | `company-005:bill-utilities-02017:1` | +3 |
| `company-005:planted-dup-0009:1` | `company-005:bill-utilities-02476:1` | +3 |
| `company-005:planted-dup-0010:1` | `company-005:bill-utilities-03168:1` | +1 |
| `company-005:planted-dup-0011:1` | `company-005:bill-variable-services-00741:1` | +3 |
| `company-005:planted-dup-0012:1` | `company-005:bill-variable-services-03169:1` | +3 |
| `company-005:planted-dup-0013:1` | `company-005:bill-variable-services-03549:1` | +2 |
| `company-006:planted-dup-0001:1` | `company-006:bill-operating-00029:1` | +3 |
| `company-006:planted-dup-0002:1` | `company-006:bill-operating-04890:1` | +1 |
| `company-006:planted-dup-0003:1` | `company-006:bill-product-development-00974:1` | +2 |
| `company-006:planted-dup-0004:1` | `company-006:bill-utilities-00390:1` | +1 |
| `company-006:planted-dup-0005:1` | `company-006:bill-utilities-01236:1` | +3 |
| `company-006:planted-dup-0006:1` | `company-006:bill-utilities-01900:1` | +2 |
| `company-006:planted-dup-0007:1` | `company-006:bill-utilities-02330:1` | +2 |
| `company-006:planted-dup-0008:1` | `company-006:bill-utilities-04140:1` | +3 |
| `company-006:planted-dup-0009:1` | `company-006:bill-variable-services-01902:1` | +2 |
| `company-006:planted-dup-0010:1` | `company-006:bill-variable-services-02474:1` | +3 |
| `company-006:planted-dup-0011:1` | `company-006:bill-variable-services-02606:1` | +1 |
| `company-007:planted-dup-0001:1` | `company-007:bill-interest-01593:1` | +1 |
| `company-007:planted-dup-0002:1` | `company-007:bill-operating-00004:1` | +3 |
| `company-007:planted-dup-0003:1` | `company-007:bill-operating-00884:1` | +1 |
| `company-007:planted-dup-0004:1` | `company-007:bill-operating-01252:1` | +1 |
| `company-007:planted-dup-0005:1` | `company-007:bill-operating-01333:1` | +3 |
| `company-007:planted-dup-0006:1` | `company-007:bill-utilities-00240:1` | +3 |
| `company-007:planted-dup-0007:1` | `company-007:bill-utilities-00485:1` | +3 |
| `company-007:planted-dup-0008:1` | `company-007:bill-utilities-00550:1` | +3 |
| `company-007:planted-dup-0009:1` | `company-007:bill-utilities-00582:1` | +2 |
| `company-007:planted-dup-0010:1` | `company-007:bill-utilities-00686:1` | +2 |
| `company-007:planted-dup-0011:1` | `company-007:bill-utilities-01249:1` | +3 |
| `company-007:planted-dup-0012:1` | `company-007:bill-utilities-01330:1` | +3 |
| `company-007:planted-dup-0013:1` | `company-007:bill-variable-services-01704:1` | +2 |
| `company-008:planted-dup-0001:1` | `company-008:bill-01811:1` | +2 |
| `company-008:planted-dup-0002:1` | `company-008:bill-02667:1` | +3 |
| `company-008:planted-dup-0003:1` | `company-008:bill-operating-00094:1` | +2 |
| `company-008:planted-dup-0004:1` | `company-008:bill-operating-01541:1` | +1 |
| `company-008:planted-dup-0005:1` | `company-008:bill-operating-01786:1` | +1 |
| `company-008:planted-dup-0006:1` | `company-008:bill-operating-02973:1` | +3 |
| `company-008:planted-dup-0007:1` | `company-008:bill-operating-03364:1` | +3 |
| `company-008:planted-dup-0008:1` | `company-008:bill-utilities-00335:1` | +2 |
| `company-008:planted-dup-0009:1` | `company-008:bill-utilities-00393:1` | +3 |
| `company-008:planted-dup-0010:1` | `company-008:bill-utilities-02251:1` | +3 |
| `company-008:planted-dup-0011:1` | `company-008:bill-utilities-02573:1` | +3 |
| `company-008:planted-dup-0012:1` | `company-008:bill-variable-services-00286:1` | +3 |
| `company-008:planted-dup-0013:1` | `company-008:bill-variable-services-03361:1` | +3 |
| `company-009:planted-dup-0001:1` | `company-009:bill-interest-02558:1` | +2 |
| `company-009:planted-dup-0002:1` | `company-009:bill-maintenance-05579:1` | +3 |
| `company-009:planted-dup-0003:1` | `company-009:bill-operating-02722:1` | +3 |
| `company-009:planted-dup-0004:1` | `company-009:bill-utilities-00064:1` | +2 |
| `company-009:planted-dup-0005:1` | `company-009:bill-utilities-03144:1` | +3 |
| `company-009:planted-dup-0006:1` | `company-009:bill-utilities-03427:1` | +2 |
| `company-010:planted-dup-0001:1` | `company-010:bill-freight-00170:1` | +3 |
| `company-010:planted-dup-0002:1` | `company-010:bill-freight-00331:1` | +3 |
| `company-010:planted-dup-0003:1` | `company-010:bill-freight-00561:1` | +3 |
| `company-010:planted-dup-0004:1` | `company-010:bill-freight-00794:1` | +2 |
| `company-010:planted-dup-0005:1` | `company-010:bill-freight-02301:1` | +3 |
| `company-010:planted-dup-0006:1` | `company-010:bill-freight-03288:1` | +3 |
| `company-010:planted-dup-0007:1` | `company-010:bill-variable-services-00539:1` | +3 |
| `company-011:planted-dup-0001:1` | `company-011:bill-00008:1` | +1 |
| `company-011:planted-dup-0002:1` | `company-011:bill-freight-00337:1` | +2 |
| `company-011:planted-dup-0003:1` | `company-011:bill-freight-00345:1` | +2 |
| `company-011:planted-dup-0004:1` | `company-011:bill-freight-00720:1` | +2 |
| `company-011:planted-dup-0005:1` | `company-011:bill-freight-01457:1` | +1 |
| `company-011:planted-dup-0006:1` | `company-011:bill-freight-02261:1` | +2 |
| `company-011:planted-dup-0007:1` | `company-011:bill-freight-02952:1` | +3 |
| `company-011:planted-dup-0008:1` | `company-011:bill-freight-03685:1` | +3 |
| `company-011:planted-dup-0009:1` | `company-011:bill-freight-03773:1` | +3 |
| `company-011:planted-dup-0010:1` | `company-011:bill-freight-04147:1` | +1 |
| `company-011:planted-dup-0011:1` | `company-011:bill-freight-04568:1` | +1 |
| `company-011:planted-dup-0012:1` | `company-011:bill-freight-04622:1` | +2 |
| `company-011:planted-dup-0013:1` | `company-011:bill-operating-01897:1` | +2 |
| `company-011:planted-dup-0014:1` | `company-011:bill-utilities-02546:1` | +2 |
| `company-012:planted-dup-0001:1` | `company-012:bill-freight-01605:1` | +3 |
| `company-012:planted-dup-0002:1` | `company-012:bill-freight-01699:1` | +1 |
| `company-012:planted-dup-0003:1` | `company-012:bill-freight-04256:1` | +2 |
| `company-012:planted-dup-0004:1` | `company-012:bill-freight-08034:1` | +1 |
| `company-012:planted-dup-0005:1` | `company-012:bill-freight-10075:1` | +2 |
| `company-012:planted-dup-0006:1` | `company-012:bill-freight-10617:1` | +2 |
| `company-012:planted-dup-0007:1` | `company-012:bill-operating-07482:1` | +1 |
| `company-013:planted-dup-0001:1` | `company-013:bill-operating-00454:1` | +3 |
| `company-013:planted-dup-0002:1` | `company-013:bill-operating-01436:1` | +1 |
| `company-013:planted-dup-0003:1` | `company-013:bill-utilities-00358:1` | +3 |
| `company-013:planted-dup-0004:1` | `company-013:bill-utilities-00963:1` | +3 |
| `company-013:planted-dup-0005:1` | `company-013:bill-utilities-01133:1` | +1 |
| `company-013:planted-dup-0006:1` | `company-013:bill-utilities-01175:1` | +3 |
| `company-014:planted-dup-0001:1` | `company-014:bill-01318:1` | +2 |
| `company-014:planted-dup-0002:1` | `company-014:bill-01345:1` | +1 |
| `company-014:planted-dup-0003:1` | `company-014:bill-operating-02218:1` | +1 |
| `company-014:planted-dup-0004:1` | `company-014:bill-utilities-00061:1` | +1 |
| `company-014:planted-dup-0005:1` | `company-014:bill-utilities-02277:1` | +1 |
| `company-014:planted-dup-0006:1` | `company-014:bill-variable-services-00264:1` | +1 |
| `company-014:planted-dup-0007:1` | `company-014:bill-variable-services-01455:1` | +3 |
| `company-014:planted-dup-0008:1` | `company-014:bill-variable-services-02151:1` | +2 |
| `company-014:planted-dup-0009:1` | `company-014:bill-variable-services-02765:1` | +2 |
| `company-015:planted-dup-0001:1` | `company-015:bill-00136:1` | +2 |
| `company-015:planted-dup-0002:1` | `company-015:bill-interest-02766:1` | +3 |
| `company-015:planted-dup-0003:1` | `company-015:bill-product-development-01104:1` | +3 |
| `company-015:planted-dup-0004:1` | `company-015:bill-utilities-00218:1` | +3 |
| `company-015:planted-dup-0005:1` | `company-015:bill-utilities-02228:1` | +3 |
| `company-015:planted-dup-0006:1` | `company-015:bill-utilities-02431:1` | +2 |
| `company-015:planted-dup-0007:1` | `company-015:bill-utilities-05136:1` | +3 |
| `company-015:planted-dup-0008:1` | `company-015:bill-utilities-05401:1` | +2 |
| `company-015:planted-dup-0009:1` | `company-015:bill-variable-services-00466:1` | +2 |
| `company-015:planted-dup-0010:1` | `company-015:bill-variable-services-05138:1` | +2 |
| `company-016:planted-dup-0001:1` | `company-016:bill-freight-00094:1` | +1 |
| `company-016:planted-dup-0002:1` | `company-016:bill-freight-00401:1` | +2 |
| `company-016:planted-dup-0003:1` | `company-016:bill-freight-00513:1` | +3 |
| `company-016:planted-dup-0004:1` | `company-016:bill-freight-00876:1` | +3 |
| `company-016:planted-dup-0005:1` | `company-016:bill-freight-01279:1` | +3 |
| `company-016:planted-dup-0006:1` | `company-016:bill-freight-02263:1` | +3 |
| `company-016:planted-dup-0007:1` | `company-016:bill-freight-02617:1` | +1 |
| `company-016:planted-dup-0008:1` | `company-016:bill-freight-02848:1` | +2 |
| `company-016:planted-dup-0009:1` | `company-016:bill-freight-03262:1` | +2 |
| `company-016:planted-dup-0010:1` | `company-016:bill-variable-services-02115:1` | +1 |
| `company-017:planted-dup-0001:1` | `company-017:bill-02994:1` | +3 |
| `company-017:planted-dup-0002:1` | `company-017:bill-freight-00328:1` | +1 |
| `company-017:planted-dup-0003:1` | `company-017:bill-freight-00396:1` | +2 |
| `company-017:planted-dup-0004:1` | `company-017:bill-freight-00542:1` | +2 |
| `company-017:planted-dup-0005:1` | `company-017:bill-freight-01278:1` | +1 |
| `company-017:planted-dup-0006:1` | `company-017:bill-freight-01662:1` | +1 |
| `company-017:planted-dup-0007:1` | `company-017:bill-freight-01751:1` | +2 |
| `company-017:planted-dup-0008:1` | `company-017:bill-freight-05280:1` | +3 |
| `company-017:planted-dup-0009:1` | `company-017:bill-freight-05430:1` | +1 |
| `company-017:planted-dup-0010:1` | `company-017:bill-freight-05728:1` | +3 |
| `company-017:planted-dup-0011:1` | `company-017:bill-freight-05783:1` | +3 |
| `company-017:planted-dup-0012:1` | `company-017:bill-freight-06630:1` | +1 |
| `company-017:planted-dup-0013:1` | `company-017:bill-utilities-04845:1` | +1 |
| `company-018:planted-dup-0001:1` | `company-018:bill-00668:1` | +1 |
| `company-018:planted-dup-0002:1` | `company-018:bill-01002:1` | +3 |
| `company-018:planted-dup-0003:1` | `company-018:bill-01624:1` | +1 |
| `company-018:planted-dup-0004:1` | `company-018:bill-01690:1` | +2 |
| `company-018:planted-dup-0005:1` | `company-018:bill-03305:1` | +2 |
| `company-018:planted-dup-0006:1` | `company-018:bill-04737:1` | +1 |
| `company-018:planted-dup-0007:1` | `company-018:bill-operating-02628:1` | +1 |
| `company-018:planted-dup-0008:1` | `company-018:bill-utilities-04947:1` | +1 |
| `company-018:planted-dup-0009:1` | `company-018:bill-variable-services-00418:1` | +1 |
| `company-018:planted-dup-0010:1` | `company-018:bill-variable-services-00903:1` | +1 |
| `company-018:planted-dup-0011:1` | `company-018:bill-variable-services-02029:1` | +2 |
| `company-018:planted-dup-0012:1` | `company-018:bill-variable-services-03793:1` | +2 |
| `company-019:planted-dup-0001:1` | `company-019:bill-interest-01951:1` | +3 |
| `company-019:planted-dup-0002:1` | `company-019:bill-operating-00614:1` | +3 |
| `company-019:planted-dup-0003:1` | `company-019:bill-utilities-00088:1` | +2 |
| `company-019:planted-dup-0004:1` | `company-019:bill-utilities-00301:1` | +1 |
| `company-019:planted-dup-0005:1` | `company-019:bill-utilities-00947:1` | +2 |
| `company-019:planted-dup-0006:1` | `company-019:bill-utilities-01604:1` | +3 |
| `company-019:planted-dup-0007:1` | `company-019:bill-variable-services-00612:1` | +1 |
| `company-020:planted-dup-0001:1` | `company-020:bill-00643:1` | +2 |
| `company-020:planted-dup-0002:1` | `company-020:bill-00851:1` | +2 |
| `company-020:planted-dup-0003:1` | `company-020:bill-00862:1` | +1 |
| `company-020:planted-dup-0004:1` | `company-020:bill-01019:1` | +1 |
| `company-020:planted-dup-0005:1` | `company-020:bill-01423:1` | +3 |
| `company-020:planted-dup-0006:1` | `company-020:bill-02158:1` | +1 |
| `company-020:planted-dup-0007:1` | `company-020:bill-03196:1` | +1 |
| `company-020:planted-dup-0008:1` | `company-020:bill-interest-02978:1` | +3 |
| `company-020:planted-dup-0009:1` | `company-020:bill-operating-02503:1` | +3 |
| `company-020:planted-dup-0010:1` | `company-020:bill-utilities-00019:1` | +3 |
| `company-020:planted-dup-0011:1` | `company-020:bill-utilities-02250:1` | +3 |
| `company-020:planted-dup-0012:1` | `company-020:bill-utilities-03381:1` | +3 |
| `company-020:planted-dup-0013:1` | `company-020:bill-variable-services-00833:1` | +1 |
| `company-021:planted-dup-0001:1` | `company-021:bill-01815:1` | +2 |
| `company-021:planted-dup-0002:1` | `company-021:bill-02437:1` | +3 |
| `company-021:planted-dup-0003:1` | `company-021:bill-05180:1` | +2 |
| `company-021:planted-dup-0004:1` | `company-021:bill-05687:1` | +2 |
| `company-021:planted-dup-0005:1` | `company-021:bill-06097:1` | +3 |
| `company-021:planted-dup-0006:1` | `company-021:bill-06372:1` | +1 |
| `company-021:planted-dup-0007:1` | `company-021:bill-income-tax-02584:1` | +2 |
| `company-021:planted-dup-0008:1` | `company-021:bill-operating-03185:1` | +3 |
| `company-021:planted-dup-0009:1` | `company-021:bill-utilities-00357:1` | +3 |
| `company-021:planted-dup-0010:1` | `company-021:bill-utilities-00903:1` | +3 |
| `company-022:planted-dup-0001:1` | `company-022:bill-00078:1` | +1 |
| `company-022:planted-dup-0002:1` | `company-022:bill-interest-00074:1` | +3 |
| `company-022:planted-dup-0003:1` | `company-022:bill-interest-00084:1` | +1 |
| `company-022:planted-dup-0004:1` | `company-022:bill-operating-00034:1` | +1 |
| `company-022:planted-dup-0005:1` | `company-022:bill-operating-00067:1` | +2 |
| `company-022:planted-dup-0006:1` | `company-022:bill-operating-00076:1` | +3 |
| `company-022:planted-dup-0007:1` | `company-022:bill-operating-00086:1` | +1 |
| `company-022:planted-dup-0008:1` | `company-022:bill-operating-00094:1` | +1 |
| `company-022:planted-dup-0009:1` | `company-022:bill-operating-00103:1` | +2 |
| `company-022:planted-dup-0010:1` | `company-022:bill-operating-00110:1` | +2 |
| `company-022:planted-dup-0011:1` | `company-022:bill-utilities-00030:1` | +2 |
| `company-022:planted-dup-0012:1` | `company-022:bill-variable-services-00017:1` | +3 |
| `company-022:planted-dup-0013:1` | `company-022:bill-variable-services-00046:1` | +2 |
| `company-023:planted-dup-0001:1` | `company-023:pbill-00012:1` | +3 |
| `company-023:planted-dup-0002:1` | `company-023:pbill-00040:1` | +2 |
| `company-023:planted-dup-0003:1` | `company-023:pbill-00047:1` | +1 |
| `company-023:planted-dup-0004:1` | `company-023:pbill-00050:1` | +2 |
| `company-023:planted-dup-0005:1` | `company-023:pbill-00052:1` | +3 |
| `company-023:planted-dup-0006:1` | `company-023:pbill-00119:1` | +3 |
| `company-023:planted-dup-0007:1` | `company-023:pbill-00136:1` | +3 |
| `company-023:planted-dup-0008:1` | `company-023:pbill-00139:1` | +2 |
| `company-023:planted-dup-0009:1` | `company-023:pbill-00146:1` | +1 |
| `company-023:planted-dup-0010:1` | `company-023:pbill-00259:1` | +2 |
| `company-023:planted-dup-0011:1` | `company-023:pbill-00397:1` | +2 |
| `company-024:planted-dup-0001:1` | `company-024:pbill-00057:1` | +3 |
| `company-024:planted-dup-0002:1` | `company-024:pbill-00173:1` | +3 |
| `company-024:planted-dup-0003:1` | `company-024:pbill-00187:1` | +2 |
| `company-024:planted-dup-0004:1` | `company-024:pbill-00247:1` | +1 |
| `company-024:planted-dup-0005:1` | `company-024:pbill-00297:1` | +1 |
| `company-024:planted-dup-0006:1` | `company-024:pbill-00356:1` | +1 |
| `company-024:planted-dup-0007:1` | `company-024:pbill-00380:1` | +3 |
| `company-024:planted-dup-0008:1` | `company-024:pbill-00389:1` | +3 |
| `company-024:planted-dup-0009:1` | `company-024:pbill-00640:1` | +2 |
| `company-025:planted-dup-0001:1` | `company-025:pbill-00007:1` | +2 |
| `company-025:planted-dup-0002:1` | `company-025:pbill-00021:1` | +2 |
| `company-025:planted-dup-0003:1` | `company-025:pbill-00022:1` | +1 |
| `company-025:planted-dup-0004:1` | `company-025:pbill-00080:1` | +3 |
| `company-025:planted-dup-0005:1` | `company-025:pbill-00085:1` | +1 |
| `company-025:planted-dup-0006:1` | `company-025:pbill-00100:1` | +3 |
| `company-025:planted-dup-0007:1` | `company-025:pbill-00103:1` | +3 |
| `company-025:planted-dup-0008:1` | `company-025:pbill-00506:1` | +2 |
| `company-025:planted-dup-0009:1` | `company-025:pbill-00535:1` | +2 |
| `company-025:planted-dup-0010:1` | `company-025:pbill-00582:1` | +3 |
| `company-025:planted-dup-0011:1` | `company-025:pbill-00711:1` | +1 |
| `company-026:planted-dup-0001:1` | `company-026:pbill-00024:1` | +3 |
| `company-026:planted-dup-0002:1` | `company-026:pbill-00059:1` | +3 |
| `company-026:planted-dup-0003:1` | `company-026:pbill-00181:1` | +3 |
| `company-026:planted-dup-0004:1` | `company-026:pbill-00313:1` | +2 |
| `company-026:planted-dup-0005:1` | `company-026:pbill-00431:1` | +1 |
| `company-027:planted-dup-0001:1` | `company-027:pbill-00140:1` | +3 |
| `company-027:planted-dup-0002:1` | `company-027:pbill-00144:1` | +2 |
| `company-027:planted-dup-0003:1` | `company-027:pbill-00147:1` | +2 |
| `company-027:planted-dup-0004:1` | `company-027:pbill-00180:1` | +2 |
| `company-027:planted-dup-0005:1` | `company-027:pbill-00191:1` | +1 |
| `company-027:planted-dup-0006:1` | `company-027:pbill-00202:1` | +1 |
| `company-027:planted-dup-0007:1` | `company-027:pbill-00395:1` | +3 |
| `company-027:planted-dup-0008:1` | `company-027:pbill-00477:1` | +3 |
| `company-027:planted-dup-0009:1` | `company-027:pbill-00615:1` | +2 |
| `company-027:planted-dup-0010:1` | `company-027:pbill-00689:1` | +1 |
| `company-027:planted-dup-0011:1` | `company-027:pbill-00740:1` | +2 |
| `company-027:planted-dup-0012:1` | `company-027:pbill-00761:1` | +1 |
| `company-027:planted-dup-0013:1` | `company-027:pbill-00767:1` | +3 |
| `company-027:planted-dup-0014:1` | `company-027:pbill-00780:1` | +2 |
| `company-027:planted-dup-0015:1` | `company-027:pbill-00807:1` | +2 |
| `company-028:planted-dup-0001:1` | `company-028:pbill-00163:1` | +1 |
| `company-028:planted-dup-0002:1` | `company-028:pbill-00863:1` | +2 |
| `company-028:planted-dup-0003:1` | `company-028:pbill-00977:1` | +1 |
| `company-028:planted-dup-0004:1` | `company-028:pbill-01036:1` | +2 |
| `company-028:planted-dup-0005:1` | `company-028:pbill-01044:1` | +1 |
| `company-028:planted-dup-0006:1` | `company-028:pbill-01089:1` | +3 |
| `company-029:planted-dup-0001:1` | `company-029:pbill-00147:1` | +2 |
| `company-029:planted-dup-0002:1` | `company-029:pbill-00204:1` | +2 |
| `company-029:planted-dup-0003:1` | `company-029:pbill-00462:1` | +2 |
| `company-029:planted-dup-0004:1` | `company-029:pbill-00648:1` | +2 |
| `company-029:planted-dup-0005:1` | `company-029:pbill-00678:1` | +3 |
| `company-029:planted-dup-0006:1` | `company-029:pbill-00685:1` | +1 |
| `company-029:planted-dup-0007:1` | `company-029:pbill-00800:1` | +3 |
| `company-030:planted-dup-0001:1` | `company-030:pbill-00080:1` | +1 |
| `company-030:planted-dup-0002:1` | `company-030:pbill-00127:1` | +2 |
| `company-030:planted-dup-0003:1` | `company-030:pbill-00144:1` | +2 |
| `company-030:planted-dup-0004:1` | `company-030:pbill-00163:1` | +3 |
| `company-030:planted-dup-0005:1` | `company-030:pbill-00176:1` | +1 |
| `company-030:planted-dup-0006:1` | `company-030:pbill-00266:1` | +1 |
| `company-030:planted-dup-0007:1` | `company-030:pbill-00273:1` | +2 |
| `company-030:planted-dup-0008:1` | `company-030:pbill-00482:1` | +1 |
| `company-030:planted-dup-0009:1` | `company-030:pbill-00519:1` | +1 |
| `company-030:planted-dup-0010:1` | `company-030:pbill-00602:1` | +3 |
| `company-030:planted-dup-0011:1` | `company-030:pbill-00645:1` | +1 |
| `company-030:planted-dup-0012:1` | `company-030:pbill-00660:1` | +2 |
| `company-031:planted-dup-0001:1` | `company-031:pbill-00056:1` | +2 |
| `company-031:planted-dup-0002:1` | `company-031:pbill-00083:1` | +1 |
| `company-031:planted-dup-0003:1` | `company-031:pbill-00135:1` | +3 |
| `company-031:planted-dup-0004:1` | `company-031:pbill-00232:1` | +1 |
| `company-031:planted-dup-0005:1` | `company-031:pbill-00326:1` | +3 |
| `company-031:planted-dup-0006:1` | `company-031:pbill-00361:1` | +1 |
| `company-031:planted-dup-0007:1` | `company-031:pbill-00377:1` | +2 |
| `company-031:planted-dup-0008:1` | `company-031:pbill-00466:1` | +3 |
| `company-031:planted-dup-0009:1` | `company-031:pbill-00495:1` | +2 |
| `company-031:planted-dup-0010:1` | `company-031:pbill-00538:1` | +3 |
| `company-031:planted-dup-0011:1` | `company-031:pbill-00542:1` | +3 |
| `company-031:planted-dup-0012:1` | `company-031:pbill-00567:1` | +2 |
| `company-031:planted-dup-0013:1` | `company-031:pbill-00586:1` | +2 |
| `company-032:planted-dup-0001:1` | `company-032:pbill-00021:1` | +1 |
| `company-032:planted-dup-0002:1` | `company-032:pbill-00062:1` | +3 |
| `company-032:planted-dup-0003:1` | `company-032:pbill-00070:1` | +2 |
| `company-032:planted-dup-0004:1` | `company-032:pbill-00086:1` | +1 |
| `company-032:planted-dup-0005:1` | `company-032:pbill-00206:1` | +2 |
| `company-032:planted-dup-0006:1` | `company-032:pbill-00214:1` | +2 |
| `company-032:planted-dup-0007:1` | `company-032:pbill-00235:1` | +1 |
| `company-033:planted-dup-0001:1` | `company-033:pbill-00015:1` | +3 |
| `company-033:planted-dup-0002:1` | `company-033:pbill-00028:1` | +3 |
| `company-033:planted-dup-0003:1` | `company-033:pbill-00124:1` | +1 |
| `company-033:planted-dup-0004:1` | `company-033:pbill-00136:1` | +1 |
| `company-033:planted-dup-0005:1` | `company-033:pbill-00172:1` | +1 |
| `company-033:planted-dup-0006:1` | `company-033:pbill-00209:1` | +1 |
| `company-033:planted-dup-0007:1` | `company-033:pbill-00255:1` | +2 |
| `company-033:planted-dup-0008:1` | `company-033:pbill-00260:1` | +2 |
| `company-033:planted-dup-0009:1` | `company-033:pbill-00276:1` | +3 |
| `company-033:planted-dup-0010:1` | `company-033:pbill-00400:1` | +1 |
| `company-033:planted-dup-0011:1` | `company-033:pbill-00444:1` | +1 |
| `company-033:planted-dup-0012:1` | `company-033:pbill-00534:1` | +3 |
| `company-033:planted-dup-0013:1` | `company-033:pbill-00537:1` | +1 |
| `company-033:planted-dup-0014:1` | `company-033:pbill-00551:1` | +1 |
| `company-034:planted-dup-0001:1` | `company-034:pbill-00006:1` | +3 |
| `company-034:planted-dup-0002:1` | `company-034:pbill-00044:1` | +1 |
| `company-034:planted-dup-0003:1` | `company-034:pbill-00086:1` | +2 |
| `company-034:planted-dup-0004:1` | `company-034:pbill-00139:1` | +3 |
| `company-034:planted-dup-0005:1` | `company-034:pbill-00174:1` | +1 |
| `company-034:planted-dup-0006:1` | `company-034:pbill-00175:1` | +1 |
| `company-034:planted-dup-0007:1` | `company-034:pbill-00204:1` | +3 |
| `company-034:planted-dup-0008:1` | `company-034:pbill-00217:1` | +3 |
| `company-034:planted-dup-0009:1` | `company-034:pbill-00227:1` | +3 |
| `company-034:planted-dup-0010:1` | `company-034:pbill-00236:1` | +1 |
| `company-034:planted-dup-0011:1` | `company-034:pbill-00355:1` | +3 |
| `company-034:planted-dup-0012:1` | `company-034:pbill-00447:1` | +1 |
| `company-034:planted-dup-0013:1` | `company-034:pbill-00461:1` | +1 |
| `company-034:planted-dup-0014:1` | `company-034:pbill-00462:1` | +1 |
| `company-034:planted-dup-0015:1` | `company-034:pbill-00506:1` | +2 |
| `company-035:planted-dup-0001:1` | `company-035:pbill-00001:1` | +2 |
| `company-035:planted-dup-0002:1` | `company-035:pbill-00031:1` | +2 |
| `company-035:planted-dup-0003:1` | `company-035:pbill-00080:1` | +1 |
| `company-035:planted-dup-0004:1` | `company-035:pbill-00139:1` | +1 |
| `company-035:planted-dup-0005:1` | `company-035:pbill-00164:1` | +2 |
| `company-035:planted-dup-0006:1` | `company-035:pbill-00182:1` | +1 |
| `company-035:planted-dup-0007:1` | `company-035:pbill-00197:1` | +2 |
| `company-035:planted-dup-0008:1` | `company-035:pbill-00226:1` | +3 |
| `company-035:planted-dup-0009:1` | `company-035:pbill-00341:1` | +2 |
| `company-035:planted-dup-0010:1` | `company-035:pbill-00382:1` | +1 |
| `company-036:planted-dup-0001:1` | `company-036:pbill-00057:1` | +3 |
| `company-036:planted-dup-0002:1` | `company-036:pbill-00059:1` | +1 |
| `company-036:planted-dup-0003:1` | `company-036:pbill-00104:1` | +1 |
| `company-036:planted-dup-0004:1` | `company-036:pbill-00134:1` | +2 |
| `company-036:planted-dup-0005:1` | `company-036:pbill-00209:1` | +2 |
| `company-036:planted-dup-0006:1` | `company-036:pbill-00277:1` | +3 |
| `company-036:planted-dup-0007:1` | `company-036:pbill-00361:1` | +2 |
| `company-036:planted-dup-0008:1` | `company-036:pbill-00406:1` | +1 |
| `company-037:planted-dup-0001:1` | `company-037:pbill-00001:1` | +3 |
| `company-037:planted-dup-0002:1` | `company-037:pbill-00032:1` | +1 |
| `company-037:planted-dup-0003:1` | `company-037:pbill-00090:1` | +1 |
| `company-037:planted-dup-0004:1` | `company-037:pbill-00236:1` | +1 |
| `company-037:planted-dup-0005:1` | `company-037:pbill-00265:1` | +3 |
| `company-037:planted-dup-0006:1` | `company-037:pbill-00403:1` | +1 |
| `company-037:planted-dup-0007:1` | `company-037:pbill-00423:1` | +1 |
| `company-037:planted-dup-0008:1` | `company-037:pbill-00456:1` | +1 |
| `company-037:planted-dup-0009:1` | `company-037:pbill-00474:1` | +1 |
| `company-037:planted-dup-0010:1` | `company-037:pbill-00596:1` | +3 |
| `company-038:planted-dup-0001:1` | `company-038:pbill-00035:1` | +3 |
| `company-038:planted-dup-0002:1` | `company-038:pbill-00057:1` | +2 |
| `company-038:planted-dup-0003:1` | `company-038:pbill-00065:1` | +3 |
| `company-038:planted-dup-0004:1` | `company-038:pbill-00101:1` | +3 |
| `company-038:planted-dup-0005:1` | `company-038:pbill-00107:1` | +1 |
| `company-038:planted-dup-0006:1` | `company-038:pbill-00206:1` | +3 |
| `company-038:planted-dup-0007:1` | `company-038:pbill-00248:1` | +1 |
| `company-038:planted-dup-0008:1` | `company-038:pbill-00337:1` | +3 |
| `company-038:planted-dup-0009:1` | `company-038:pbill-00399:1` | +1 |
| `company-038:planted-dup-0010:1` | `company-038:pbill-00450:1` | +1 |
| `company-038:planted-dup-0011:1` | `company-038:pbill-00466:1` | +2 |
| `company-038:planted-dup-0012:1` | `company-038:pbill-00496:1` | +1 |
| `company-038:planted-dup-0013:1` | `company-038:pbill-00556:1` | +3 |
| `company-038:planted-dup-0014:1` | `company-038:pbill-00589:1` | +2 |
| `company-038:planted-dup-0015:1` | `company-038:pbill-00632:1` | +1 |
| `company-039:planted-dup-0001:1` | `company-039:pbill-00118:1` | +3 |
| `company-039:planted-dup-0002:1` | `company-039:pbill-00143:1` | +1 |
| `company-039:planted-dup-0003:1` | `company-039:pbill-00194:1` | +2 |
| `company-039:planted-dup-0004:1` | `company-039:pbill-00256:1` | +1 |
| `company-039:planted-dup-0005:1` | `company-039:pbill-00378:1` | +1 |
| `company-039:planted-dup-0006:1` | `company-039:pbill-00382:1` | +1 |
| `company-039:planted-dup-0007:1` | `company-039:pbill-00419:1` | +1 |
| `company-039:planted-dup-0008:1` | `company-039:pbill-00449:1` | +1 |
| `company-039:planted-dup-0009:1` | `company-039:pbill-00504:1` | +1 |
| `company-039:planted-dup-0010:1` | `company-039:pbill-00518:1` | +3 |
| `company-039:planted-dup-0011:1` | `company-039:pbill-00582:1` | +3 |
| `company-040:planted-dup-0001:1` | `company-040:pbill-00028:1` | +1 |
| `company-040:planted-dup-0002:1` | `company-040:pbill-00129:1` | +3 |
| `company-040:planted-dup-0003:1` | `company-040:pbill-00168:1` | +1 |
| `company-040:planted-dup-0004:1` | `company-040:pbill-00192:1` | +2 |
| `company-040:planted-dup-0005:1` | `company-040:pbill-00260:1` | +1 |
| `company-040:planted-dup-0006:1` | `company-040:pbill-00296:1` | +2 |
| `company-040:planted-dup-0007:1` | `company-040:pbill-00323:1` | +2 |
| `company-040:planted-dup-0008:1` | `company-040:pbill-00489:1` | +3 |
| `company-040:planted-dup-0009:1` | `company-040:pbill-00507:1` | +3 |
| `company-040:planted-dup-0010:1` | `company-040:pbill-00523:1` | +3 |
| `company-040:planted-dup-0011:1` | `company-040:pbill-00542:1` | +1 |
| `company-040:planted-dup-0012:1` | `company-040:pbill-00633:1` | +1 |
| `company-040:planted-dup-0013:1` | `company-040:pbill-00790:1` | +2 |
| `company-040:planted-dup-0014:1` | `company-040:pbill-00848:1` | +2 |
| `company-041:planted-dup-0001:1` | `company-041:pbill-00106:1` | +1 |
| `company-041:planted-dup-0002:1` | `company-041:pbill-00200:1` | +2 |
| `company-041:planted-dup-0003:1` | `company-041:pbill-00221:1` | +1 |
| `company-041:planted-dup-0004:1` | `company-041:pbill-00271:1` | +3 |
| `company-041:planted-dup-0005:1` | `company-041:pbill-00301:1` | +3 |
| `company-041:planted-dup-0006:1` | `company-041:pbill-00303:1` | +3 |
| `company-041:planted-dup-0007:1` | `company-041:pbill-00400:1` | +1 |
| `company-041:planted-dup-0008:1` | `company-041:pbill-00483:1` | +2 |
| `company-041:planted-dup-0009:1` | `company-041:pbill-00510:1` | +1 |
| `company-041:planted-dup-0010:1` | `company-041:pbill-00573:1` | +1 |
| `company-042:planted-dup-0001:1` | `company-042:pbill-00117:1` | +2 |
| `company-042:planted-dup-0002:1` | `company-042:pbill-00139:1` | +3 |
| `company-042:planted-dup-0003:1` | `company-042:pbill-00142:1` | +1 |
| `company-042:planted-dup-0004:1` | `company-042:pbill-00147:1` | +3 |
| `company-042:planted-dup-0005:1` | `company-042:pbill-00230:1` | +1 |
| `company-042:planted-dup-0006:1` | `company-042:pbill-00259:1` | +1 |
| `company-042:planted-dup-0007:1` | `company-042:pbill-00301:1` | +3 |
| `company-042:planted-dup-0008:1` | `company-042:pbill-00367:1` | +1 |
| `company-042:planted-dup-0009:1` | `company-042:pbill-00381:1` | +3 |
| `company-042:planted-dup-0010:1` | `company-042:pbill-00450:1` | +3 |
| `company-042:planted-dup-0011:1` | `company-042:pbill-00479:1` | +3 |
| `company-042:planted-dup-0012:1` | `company-042:pbill-00487:1` | +3 |
| `company-042:planted-dup-0013:1` | `company-042:pbill-00539:1` | +1 |
| `company-043:planted-dup-0001:1` | `company-043:pbill-00236:1` | +2 |
| `company-043:planted-dup-0002:1` | `company-043:pbill-00302:1` | +2 |
| `company-043:planted-dup-0003:1` | `company-043:pbill-00349:1` | +2 |
| `company-043:planted-dup-0004:1` | `company-043:pbill-00362:1` | +2 |
| `company-043:planted-dup-0005:1` | `company-043:pbill-00546:1` | +2 |
| `company-043:planted-dup-0006:1` | `company-043:pbill-00608:1` | +2 |
| `company-043:planted-dup-0007:1` | `company-043:pbill-00621:1` | +3 |
| `company-043:planted-dup-0008:1` | `company-043:pbill-00689:1` | +3 |
| `company-044:planted-dup-0001:1` | `company-044:pbill-00140:1` | +1 |
| `company-044:planted-dup-0002:1` | `company-044:pbill-00155:1` | +3 |
| `company-044:planted-dup-0003:1` | `company-044:pbill-00216:1` | +2 |
| `company-044:planted-dup-0004:1` | `company-044:pbill-00252:1` | +2 |
| `company-044:planted-dup-0005:1` | `company-044:pbill-00270:1` | +1 |
| `company-044:planted-dup-0006:1` | `company-044:pbill-00296:1` | +3 |
| `company-044:planted-dup-0007:1` | `company-044:pbill-00352:1` | +1 |
| `company-044:planted-dup-0008:1` | `company-044:pbill-00375:1` | +2 |
| `company-044:planted-dup-0009:1` | `company-044:pbill-00394:1` | +3 |
| `company-044:planted-dup-0010:1` | `company-044:pbill-00411:1` | +2 |
| `company-044:planted-dup-0011:1` | `company-044:pbill-00433:1` | +3 |
| `company-045:planted-dup-0001:1` | `company-045:bill-operating-00140:1` | +2 |
| `company-045:planted-dup-0002:1` | `company-045:bill-operating-00265:1` | +3 |
| `company-045:planted-dup-0003:1` | `company-045:bill-operating-00365:1` | +2 |
| `company-045:planted-dup-0004:1` | `company-045:bill-utilities-00182:1` | +2 |
| `company-045:planted-dup-0005:1` | `company-045:bill-utilities-00193:1` | +3 |
| `company-045:planted-dup-0006:1` | `company-045:bill-utilities-00340:1` | +2 |
| `company-045:planted-dup-0007:1` | `company-045:bill-utilities-00370:1` | +1 |
| `company-045:planted-dup-0008:1` | `company-045:pbill-00097:1` | +2 |
| `company-045:planted-dup-0009:1` | `company-045:pbill-00153:1` | +2 |
| `company-045:planted-dup-0010:1` | `company-045:pbill-00201:1` | +3 |
| `company-045:planted-dup-0011:1` | `company-045:pbill-00216:1` | +1 |
| `company-045:planted-dup-0012:1` | `company-045:pbill-00241:1` | +2 |
| `company-046:planted-dup-0001:1` | `company-046:bill-00354:1` | +2 |
| `company-046:planted-dup-0002:1` | `company-046:bill-variable-services-00661:1` | +3 |
| `company-046:planted-dup-0003:1` | `company-046:pbill-00001:1` | +3 |
| `company-046:planted-dup-0004:1` | `company-046:pbill-00231:1` | +2 |
| `company-046:planted-dup-0005:1` | `company-046:pbill-00261:1` | +2 |
| `company-046:planted-dup-0006:1` | `company-046:pbill-00323:1` | +3 |
| `company-047:planted-dup-0001:1` | `company-047:bill-operating-00533:1` | +3 |
| `company-047:planted-dup-0002:1` | `company-047:bill-utilities-00124:1` | +2 |
| `company-047:planted-dup-0003:1` | `company-047:pbill-00044:1` | +2 |
| `company-047:planted-dup-0004:1` | `company-047:pbill-00115:1` | +3 |
| `company-047:planted-dup-0005:1` | `company-047:pbill-00191:1` | +3 |
| `company-047:planted-dup-0006:1` | `company-047:pbill-00256:1` | +3 |
| `company-048:planted-dup-0001:1` | `company-048:bill-00099:1` | +1 |
| `company-048:planted-dup-0002:1` | `company-048:bill-00381:1` | +3 |
| `company-048:planted-dup-0003:1` | `company-048:bill-operating-00092:1` | +1 |
| `company-048:planted-dup-0004:1` | `company-048:bill-operating-00189:1` | +2 |
| `company-048:planted-dup-0005:1` | `company-048:bill-operating-00194:1` | +1 |
| `company-048:planted-dup-0006:1` | `company-048:bill-operating-00343:1` | +1 |
| `company-048:planted-dup-0007:1` | `company-048:bill-operating-00358:1` | +1 |
| `company-048:planted-dup-0008:1` | `company-048:bill-utilities-00072:1` | +2 |
| `company-048:planted-dup-0009:1` | `company-048:bill-utilities-00141:1` | +2 |
| `company-048:planted-dup-0010:1` | `company-048:pbill-00053:1` | +2 |
| `company-048:planted-dup-0011:1` | `company-048:pbill-00240:1` | +3 |
| `company-049:planted-dup-0001:1` | `company-049:bill-00143:1` | +1 |
| `company-049:planted-dup-0002:1` | `company-049:bill-00250:1` | +1 |
| `company-049:planted-dup-0003:1` | `company-049:bill-00499:1` | +1 |
| `company-049:planted-dup-0004:1` | `company-049:bill-operating-00024:1` | +2 |
| `company-049:planted-dup-0005:1` | `company-049:bill-utilities-00093:1` | +3 |
| `company-049:planted-dup-0006:1` | `company-049:bill-utilities-00212:1` | +3 |
| `company-049:planted-dup-0007:1` | `company-049:bill-utilities-00314:1` | +2 |
| `company-049:planted-dup-0008:1` | `company-049:bill-utilities-00332:1` | +3 |
| `company-049:planted-dup-0009:1` | `company-049:bill-variable-services-00246:1` | +1 |
| `company-049:planted-dup-0010:1` | `company-049:bill-variable-services-00498:1` | +3 |
| `company-049:planted-dup-0011:1` | `company-049:pbill-00013:1` | +1 |
| `company-049:planted-dup-0012:1` | `company-049:pbill-00046:1` | +1 |
| `company-049:planted-dup-0013:1` | `company-049:pbill-00073:1` | +1 |
| `company-049:planted-dup-0014:1` | `company-049:pbill-00183:1` | +2 |
| `company-049:planted-dup-0015:1` | `company-049:pbill-00324:1` | +2 |
| `company-050:planted-dup-0001:1` | `company-050:bill-00043:1` | +1 |
| `company-050:planted-dup-0002:1` | `company-050:bill-00340:1` | +2 |
| `company-050:planted-dup-0003:1` | `company-050:bill-utilities-00413:1` | +2 |
| `company-050:planted-dup-0004:1` | `company-050:bill-utilities-01211:1` | +3 |
| `company-050:planted-dup-0005:1` | `company-050:bill-variable-services-00893:1` | +3 |
| `company-050:planted-dup-0006:1` | `company-050:pbill-00134:1` | +2 |
| `company-051:planted-dup-0001:1` | `company-051:bill-utilities-00453:1` | +2 |
| `company-051:planted-dup-0002:1` | `company-051:bill-variable-services-00343:1` | +3 |
| `company-051:planted-dup-0003:1` | `company-051:pbill-00111:1` | +2 |
| `company-051:planted-dup-0004:1` | `company-051:pbill-00268:1` | +2 |
| `company-051:planted-dup-0005:1` | `company-051:pbill-00339:1` | +2 |
| `company-051:planted-dup-0006:1` | `company-051:pbill-00345:1` | +2 |
| `company-052:planted-dup-0001:1` | `company-052:bill-utilities-00085:1` | +1 |
| `company-052:planted-dup-0002:1` | `company-052:bill-utilities-00423:1` | +1 |
| `company-052:planted-dup-0003:1` | `company-052:pbill-00359:1` | +1 |
| `company-052:planted-dup-0004:1` | `company-052:pbill-00399:1` | +1 |
| `company-052:planted-dup-0005:1` | `company-052:pbill-00453:1` | +1 |
| `company-052:planted-dup-0006:1` | `company-052:pbill-00474:1` | +3 |
| `company-053:planted-dup-0001:1` | `company-053:bill-00451:1` | +2 |
| `company-053:planted-dup-0002:1` | `company-053:bill-00694:1` | +1 |
| `company-053:planted-dup-0003:1` | `company-053:bill-01290:1` | +3 |
| `company-053:planted-dup-0004:1` | `company-053:bill-utilities-00637:1` | +2 |
| `company-053:planted-dup-0005:1` | `company-053:bill-utilities-00773:1` | +2 |
| `company-053:planted-dup-0006:1` | `company-053:bill-utilities-00830:1` | +3 |
| `company-053:planted-dup-0007:1` | `company-053:bill-utilities-01079:1` | +3 |
| `company-053:planted-dup-0008:1` | `company-053:bill-variable-services-01236:1` | +3 |
| `company-053:planted-dup-0009:1` | `company-053:pbill-00075:1` | +1 |
| `company-054:planted-dup-0001:1` | `company-054:bill-00348:1` | +2 |
| `company-054:planted-dup-0002:1` | `company-054:bill-00355:1` | +2 |
| `company-054:planted-dup-0003:1` | `company-054:bill-utilities-00170:1` | +1 |
| `company-054:planted-dup-0004:1` | `company-054:bill-utilities-00331:1` | +1 |
| `company-054:planted-dup-0005:1` | `company-054:bill-utilities-00572:1` | +2 |
| `company-054:planted-dup-0006:1` | `company-054:bill-variable-services-00344:1` | +2 |
| `company-054:planted-dup-0007:1` | `company-054:pbill-00031:1` | +2 |
| `company-054:planted-dup-0008:1` | `company-054:pbill-00388:1` | +2 |
| `company-055:planted-dup-0001:1` | `company-055:bill-00494:1` | +1 |
| `company-055:planted-dup-0002:1` | `company-055:bill-operating-00071:1` | +2 |
| `company-055:planted-dup-0003:1` | `company-055:bill-operating-00215:1` | +2 |
| `company-055:planted-dup-0004:1` | `company-055:bill-operating-00326:1` | +3 |
| `company-055:planted-dup-0005:1` | `company-055:bill-utilities-00506:1` | +2 |
| `company-055:planted-dup-0006:1` | `company-055:pbill-00146:1` | +3 |
| `company-055:planted-dup-0007:1` | `company-055:pbill-00217:1` | +1 |
| `company-055:planted-dup-0008:1` | `company-055:pbill-00244:1` | +3 |
| `company-055:planted-dup-0009:1` | `company-055:pbill-00414:1` | +2 |
| `company-055:planted-dup-0010:1` | `company-055:pbill-00416:1` | +2 |
| `company-056:planted-dup-0001:1` | `company-056:bill-00862:1` | +1 |
| `company-056:planted-dup-0002:1` | `company-056:bill-01239:1` | +1 |
| `company-056:planted-dup-0003:1` | `company-056:bill-interest-01324:1` | +1 |
| `company-056:planted-dup-0004:1` | `company-056:bill-operating-00970:1` | +2 |
| `company-056:planted-dup-0005:1` | `company-056:bill-utilities-00301:1` | +3 |
| `company-056:planted-dup-0006:1` | `company-056:bill-utilities-00776:1` | +2 |
| `company-056:planted-dup-0007:1` | `company-056:bill-utilities-01391:1` | +3 |
| `company-056:planted-dup-0008:1` | `company-056:bill-variable-services-01293:1` | +3 |
| `company-056:planted-dup-0009:1` | `company-056:pbill-00320:1` | +2 |
| `company-057:planted-dup-0001:1` | `company-057:bill-insurance-00069:1` | +1 |
| `company-057:planted-dup-0002:1` | `company-057:bill-premises-00067:1` | +1 |
| `company-057:planted-dup-0003:1` | `company-057:bill-premises-00107:1` | +3 |
| `company-057:planted-dup-0004:1` | `company-057:bill-software-00049:1` | +1 |
| `company-057:planted-dup-0005:1` | `company-057:bill-software-00167:1` | +2 |
| `company-057:planted-dup-0006:1` | `company-057:bill-software-00186:1` | +1 |
| `company-058:planted-dup-0001:1` | `company-058:bill-insurance-00272:1` | +1 |
| `company-058:planted-dup-0002:1` | `company-058:bill-premises-00187:1` | +1 |
| `company-058:planted-dup-0003:1` | `company-058:bill-premises-00228:1` | +2 |
| `company-058:planted-dup-0004:1` | `company-058:bill-premises-00317:1` | +1 |
| `company-058:planted-dup-0005:1` | `company-058:bill-premises-00499:1` | +3 |
| `company-058:planted-dup-0006:1` | `company-058:bill-software-00143:1` | +1 |
| `company-058:planted-dup-0007:1` | `company-058:bill-software-00362:1` | +1 |
| `company-059:planted-dup-0001:1` | `company-059:bill-income-tax-00549:1` | +1 |
| `company-059:planted-dup-0002:1` | `company-059:bill-insurance-00640:1` | +3 |
| `company-059:planted-dup-0003:1` | `company-059:bill-insurance-00997:1` | +1 |
| `company-059:planted-dup-0004:1` | `company-059:bill-premises-00209:1` | +3 |
| `company-059:planted-dup-0005:1` | `company-059:bill-premises-00290:1` | +1 |
| `company-059:planted-dup-0006:1` | `company-059:bill-software-00380:1` | +3 |
| `company-059:planted-dup-0007:1` | `company-059:bill-software-00901:1` | +2 |
| `company-060:planted-dup-0001:1` | `company-060:bill-income-tax-00129:1` | +1 |
| `company-060:planted-dup-0002:1` | `company-060:bill-insurance-00031:1` | +2 |
| `company-060:planted-dup-0003:1` | `company-060:bill-insurance-00149:1` | +3 |
| `company-060:planted-dup-0004:1` | `company-060:bill-premises-00048:1` | +3 |
| `company-060:planted-dup-0005:1` | `company-060:bill-premises-00088:1` | +1 |
| `company-060:planted-dup-0006:1` | `company-060:bill-premises-00147:1` | +1 |
| `company-060:planted-dup-0007:1` | `company-060:bill-software-00206:1` | +2 |
| `company-061:planted-dup-0001:1` | `company-061:bill-insurance-00144:1` | +3 |
| `company-061:planted-dup-0002:1` | `company-061:bill-insurance-00277:1` | +2 |
| `company-061:planted-dup-0003:1` | `company-061:bill-premises-00061:1` | +3 |
| `company-061:planted-dup-0004:1` | `company-061:bill-premises-00231:1` | +3 |
| `company-061:planted-dup-0005:1` | `company-061:bill-software-00232:1` | +2 |
| `company-061:planted-dup-0006:1` | `company-061:bill-software-00461:1` | +1 |
| `company-062:planted-dup-0001:1` | `company-062:bill-income-tax-00284:1` | +2 |
| `company-062:planted-dup-0002:1` | `company-062:bill-insurance-00707:1` | +1 |
| `company-062:planted-dup-0003:1` | `company-062:bill-insurance-00770:1` | +1 |
| `company-062:planted-dup-0004:1` | `company-062:bill-insurance-00837:1` | +1 |
| `company-062:planted-dup-0005:1` | `company-062:bill-premises-00432:1` | +1 |
| `company-062:planted-dup-0006:1` | `company-062:bill-premises-00573:1` | +2 |
| `company-062:planted-dup-0007:1` | `company-062:bill-premises-00705:1` | +1 |
| `company-062:planted-dup-0008:1` | `company-062:bill-software-00207:1` | +2 |
| `company-062:planted-dup-0009:1` | `company-062:bill-software-00282:1` | +3 |
| `company-062:planted-dup-0010:1` | `company-062:bill-software-00362:1` | +3 |
| `company-062:planted-dup-0011:1` | `company-062:bill-software-00500:1` | +1 |
| `company-062:planted-dup-0012:1` | `company-062:bill-software-00706:1` | +3 |
| `company-062:planted-dup-0013:1` | `company-062:bill-software-00769:1` | +1 |
| `company-062:planted-dup-0014:1` | `company-062:bill-software-00897:1` | +1 |
| `company-063:planted-dup-0001:1` | `company-063:bill-income-tax-00073:1` | +1 |
| `company-063:planted-dup-0002:1` | `company-063:bill-income-tax-00205:1` | +1 |
| `company-063:planted-dup-0003:1` | `company-063:bill-insurance-00051:1` | +3 |
| `company-063:planted-dup-0004:1` | `company-063:bill-insurance-00094:1` | +1 |
| `company-063:planted-dup-0005:1` | `company-063:bill-insurance-00268:1` | +2 |
| `company-063:planted-dup-0006:1` | `company-063:bill-premises-00029:1` | +3 |
| `company-063:planted-dup-0007:1` | `company-063:bill-premises-00070:1` | +1 |
| `company-063:planted-dup-0008:1` | `company-063:bill-premises-00180:1` | +2 |
| `company-063:planted-dup-0009:1` | `company-063:bill-premises-00202:1` | +3 |
| `company-063:planted-dup-0010:1` | `company-063:bill-premises-00245:1` | +3 |
| `company-063:planted-dup-0011:1` | `company-063:bill-premises-00266:1` | +1 |
| `company-063:planted-dup-0012:1` | `company-063:bill-software-00030:1` | +2 |
| `company-063:planted-dup-0013:1` | `company-063:bill-software-00050:1` | +3 |
| `company-063:planted-dup-0014:1` | `company-063:bill-software-00159:1` | +1 |
| `company-064:planted-dup-0001:1` | `company-064:bill-income-tax-00186:1` | +3 |
| `company-064:planted-dup-0002:1` | `company-064:bill-insurance-00047:1` | +2 |
| `company-064:planted-dup-0003:1` | `company-064:bill-insurance-00185:1` | +1 |
| `company-064:planted-dup-0004:1` | `company-064:bill-insurance-00263:1` | +3 |
| `company-064:planted-dup-0005:1` | `company-064:bill-premises-00100:1` | +1 |
| `company-064:planted-dup-0006:1` | `company-064:bill-premises-00183:1` | +1 |
| `company-064:planted-dup-0007:1` | `company-064:bill-premises-00287:1` | +1 |
| `company-064:planted-dup-0008:1` | `company-064:bill-software-00046:1` | +2 |
| `company-064:planted-dup-0009:1` | `company-064:bill-software-00131:1` | +3 |
| `company-064:planted-dup-0010:1` | `company-064:bill-software-00212:1` | +2 |
| `company-064:planted-dup-0011:1` | `company-064:bill-software-00237:1` | +3 |
| `company-064:planted-dup-0012:1` | `company-064:bill-software-00262:1` | +3 |
| `company-064:planted-dup-0013:1` | `company-064:bill-software-00338:1` | +1 |
| `company-065:planted-dup-0001:1` | `company-065:bill-income-tax-00591:1` | +1 |
| `company-065:planted-dup-0002:1` | `company-065:bill-insurance-00213:1` | +2 |
| `company-065:planted-dup-0003:1` | `company-065:bill-insurance-00404:1` | +2 |
| `company-065:planted-dup-0004:1` | `company-065:bill-insurance-00710:1` | +3 |
| `company-065:planted-dup-0005:1` | `company-065:bill-premises-00277:1` | +2 |
| `company-065:planted-dup-0006:1` | `company-065:bill-premises-00402:1` | +1 |
| `company-065:planted-dup-0007:1` | `company-065:bill-premises-00467:1` | +3 |
| `company-065:planted-dup-0008:1` | `company-065:bill-premises-00588:1` | +1 |
| `company-065:planted-dup-0009:1` | `company-065:bill-premises-00646:1` | +3 |
| `company-065:planted-dup-0010:1` | `company-065:bill-software-00212:1` | +3 |
| `company-065:planted-dup-0011:1` | `company-065:bill-software-00589:1` | +3 |
| `company-066:planted-dup-0001:1` | `company-066:bill-income-tax-00112:1` | +3 |
| `company-066:planted-dup-0002:1` | `company-066:bill-insurance-00078:1` | +3 |
| `company-066:planted-dup-0003:1` | `company-066:bill-insurance-00147:1` | +3 |
| `company-066:planted-dup-0004:1` | `company-066:bill-insurance-00163:1` | +2 |
| `company-066:planted-dup-0005:1` | `company-066:bill-insurance-00199:1` | +3 |
| `company-066:planted-dup-0006:1` | `company-066:bill-insurance-00216:1` | +1 |
| `company-066:planted-dup-0007:1` | `company-066:bill-premises-00093:1` | +2 |
| `company-066:planted-dup-0008:1` | `company-066:bill-software-00041:1` | +2 |
| `company-066:planted-dup-0009:1` | `company-066:bill-software-00058:1` | +2 |
| `company-066:planted-dup-0010:1` | `company-066:bill-software-00077:1` | +1 |
| `company-066:planted-dup-0011:1` | `company-066:bill-software-00162:1` | +3 |
| `company-067:planted-dup-0001:1` | `company-067:bill-insurance-00047:1` | +1 |
| `company-067:planted-dup-0002:1` | `company-067:bill-insurance-00333:1` | +2 |
| `company-067:planted-dup-0003:1` | `company-067:bill-premises-00076:1` | +2 |
| `company-067:planted-dup-0004:1` | `company-067:bill-premises-00172:1` | +3 |
| `company-067:planted-dup-0005:1` | `company-067:bill-premises-00237:1` | +2 |
| `company-067:planted-dup-0006:1` | `company-067:bill-software-00046:1` | +2 |
| `company-067:planted-dup-0007:1` | `company-067:bill-software-00108:1` | +1 |
| `company-067:planted-dup-0008:1` | `company-067:bill-software-00300:1` | +1 |
| `company-067:planted-dup-0009:1` | `company-067:bill-software-00332:1` | +1 |
| `company-067:planted-dup-0010:1` | `company-067:bill-software-00396:1` | +3 |
| `company-068:planted-dup-0001:1` | `company-068:bill-income-tax-00220:1` | +3 |
| `company-068:planted-dup-0002:1` | `company-068:bill-insurance-00350:1` | +1 |
| `company-068:planted-dup-0003:1` | `company-068:bill-insurance-00413:1` | +3 |
| `company-068:planted-dup-0004:1` | `company-068:bill-premises-00736:1` | +2 |
| `company-068:planted-dup-0005:1` | `company-068:bill-software-00218:1` | +2 |
| `company-068:planted-dup-0006:1` | `company-068:bill-software-00670:1` | +2 |
| `company-069:planted-dup-0001:1` | `company-069:bill-insurance-00026:1` | +3 |
| `company-069:planted-dup-0002:1` | `company-069:bill-insurance-00128:1` | +1 |
| `company-069:planted-dup-0003:1` | `company-069:bill-insurance-00150:1` | +2 |
| `company-069:planted-dup-0004:1` | `company-069:bill-insurance-00190:1` | +3 |
| `company-069:planted-dup-0005:1` | `company-069:bill-premises-00035:1` | +3 |
| `company-069:planted-dup-0006:1` | `company-069:bill-premises-00086:1` | +2 |
| `company-069:planted-dup-0007:1` | `company-069:bill-premises-00126:1` | +3 |
| `company-069:planted-dup-0008:1` | `company-069:bill-premises-00175:1` | +3 |
| `company-069:planted-dup-0009:1` | `company-069:bill-software-00036:1` | +3 |
| `company-069:planted-dup-0010:1` | `company-069:bill-software-00087:1` | +3 |
| `company-069:planted-dup-0011:1` | `company-069:bill-software-00098:1` | +2 |
| `company-070:planted-dup-0001:1` | `company-070:bill-income-tax-00179:1` | +3 |
| `company-070:planted-dup-0002:1` | `company-070:bill-insurance-00049:1` | +3 |
| `company-070:planted-dup-0003:1` | `company-070:bill-insurance-00068:1` | +3 |
| `company-070:planted-dup-0004:1` | `company-070:bill-insurance-00229:1` | +1 |
| `company-070:planted-dup-0005:1` | `company-070:bill-insurance-00267:1` | +3 |
| `company-070:planted-dup-0006:1` | `company-070:bill-insurance-00313:1` | +3 |
| `company-070:planted-dup-0007:1` | `company-070:bill-premises-00138:1` | +3 |
| `company-070:planted-dup-0008:1` | `company-070:bill-premises-00157:1` | +2 |
| `company-070:planted-dup-0009:1` | `company-070:bill-premises-00227:1` | +2 |
| `company-070:planted-dup-0010:1` | `company-070:bill-premises-00246:1` | +2 |
| `company-070:planted-dup-0011:1` | `company-070:bill-software-00067:1` | +3 |
| `company-070:planted-dup-0012:1` | `company-070:bill-software-00177:1` | +2 |
| `company-070:planted-dup-0013:1` | `company-070:bill-software-00312:1` | +2 |
| `company-071:planted-dup-0001:1` | `company-071:bill-income-tax-00541:1` | +2 |
| `company-071:planted-dup-0002:1` | `company-071:bill-insurance-00136:1` | +1 |
| `company-071:planted-dup-0003:1` | `company-071:bill-insurance-00688:1` | +3 |
| `company-071:planted-dup-0004:1` | `company-071:bill-insurance-00726:1` | +1 |
| `company-071:planted-dup-0005:1` | `company-071:bill-software-00280:1` | +1 |
| `company-071:planted-dup-0006:1` | `company-071:bill-software-00687:1` | +1 |
| `company-072:planted-dup-0001:1` | `company-072:bill-insurance-00037:1` | +3 |
| `company-072:planted-dup-0002:1` | `company-072:bill-insurance-00206:1` | +2 |
| `company-072:planted-dup-0003:1` | `company-072:bill-premises-00098:1` | +2 |
| `company-072:planted-dup-0004:1` | `company-072:bill-premises-00139:1` | +3 |
| `company-072:planted-dup-0005:1` | `company-072:bill-premises-00151:1` | +3 |
| `company-072:planted-dup-0006:1` | `company-072:bill-software-00140:1` | +2 |
| `company-072:planted-dup-0007:1` | `company-072:bill-software-00179:1` | +1 |
| `company-073:planted-dup-0001:1` | `company-073:bill-income-tax-00046:1` | +2 |
| `company-073:planted-dup-0002:1` | `company-073:bill-insurance-00015:1` | +3 |
| `company-073:planted-dup-0003:1` | `company-073:bill-insurance-00079:1` | +2 |
| `company-073:planted-dup-0004:1` | `company-073:bill-insurance-00128:1` | +1 |
| `company-073:planted-dup-0005:1` | `company-073:bill-insurance-00159:1` | +2 |
| `company-073:planted-dup-0006:1` | `company-073:bill-premises-00013:1` | +1 |
| `company-073:planted-dup-0007:1` | `company-073:bill-premises-00077:1` | +3 |
| `company-073:planted-dup-0008:1` | `company-073:bill-supplies-00040:1` | +3 |
| `company-073:planted-dup-0009:1` | `company-073:bill-supplies-00056:1` | +2 |
| `company-073:planted-dup-0010:1` | `company-073:bill-supplies-00117:1` | +1 |
| `company-073:planted-dup-0011:1` | `company-073:bill-supplies-00120:1` | +1 |
| `company-073:planted-dup-0012:1` | `company-073:bill-supplies-00135:1` | +1 |
| `company-073:planted-dup-0013:1` | `company-073:bill-supplies-00138:1` | +2 |
| `company-074:planted-dup-0001:1` | `company-074:bill-insurance-00169:1` | +2 |
| `company-074:planted-dup-0002:1` | `company-074:bill-insurance-00285:1` | +1 |
| `company-074:planted-dup-0003:1` | `company-074:bill-premises-00228:1` | +3 |
| `company-074:planted-dup-0004:1` | `company-074:bill-supplies-00089:1` | +3 |
| `company-074:planted-dup-0005:1` | `company-074:bill-supplies-00161:1` | +2 |
| `company-074:planted-dup-0006:1` | `company-074:bill-supplies-00249:1` | +1 |
| `company-074:planted-dup-0007:1` | `company-074:bill-utilities-00053:1` | +1 |
| `company-075:planted-dup-0001:1` | `company-075:bill-insurance-00217:1` | +2 |
| `company-075:planted-dup-0002:1` | `company-075:bill-insurance-00268:1` | +1 |
| `company-075:planted-dup-0003:1` | `company-075:bill-insurance-00383:1` | +1 |
| `company-075:planted-dup-0004:1` | `company-075:bill-insurance-00434:1` | +3 |
| `company-075:planted-dup-0005:1` | `company-075:bill-supplies-00064:1` | +2 |
| `company-075:planted-dup-0006:1` | `company-075:bill-supplies-00088:1` | +3 |
| `company-075:planted-dup-0007:1` | `company-075:bill-supplies-00230:1` | +1 |
| `company-075:planted-dup-0008:1` | `company-075:bill-supplies-00242:1` | +1 |
| `company-075:planted-dup-0009:1` | `company-075:bill-supplies-00408:1` | +1 |
| `company-075:planted-dup-0010:1` | `company-075:bill-supplies-00499:1` | +3 |
| `company-075:planted-dup-0011:1` | `company-075:bill-supplies-00511:1` | +1 |
| `company-075:planted-dup-0012:1` | `company-075:bill-supplies-00574:1` | +2 |
| `company-075:planted-dup-0013:1` | `company-075:bill-supplies-00613:1` | +1 |
| `company-075:planted-dup-0014:1` | `company-075:bill-supplies-00637:1` | +3 |
| `company-076:planted-dup-0001:1` | `company-076:bill-insurance-00045:1` | +3 |
| `company-076:planted-dup-0002:1` | `company-076:bill-insurance-00064:1` | +2 |
| `company-076:planted-dup-0003:1` | `company-076:bill-premises-00077:1` | +2 |
| `company-076:planted-dup-0004:1` | `company-076:bill-premises-00189:1` | +3 |
| `company-076:planted-dup-0005:1` | `company-076:bill-supplies-00004:1` | +2 |
| `company-076:planted-dup-0006:1` | `company-076:bill-supplies-00016:1` | +3 |
| `company-076:planted-dup-0007:1` | `company-076:bill-supplies-00022:1` | +1 |
| `company-076:planted-dup-0008:1` | `company-076:bill-supplies-00132:1` | +2 |
| `company-076:planted-dup-0009:1` | `company-076:bill-utilities-00044:1` | +3 |
| `company-076:planted-dup-0010:1` | `company-076:bill-utilities-00142:1` | +2 |
| `company-076:planted-dup-0011:1` | `company-076:bill-utilities-00157:1` | +1 |
| `company-077:planted-dup-0001:1` | `company-077:bill-insurance-00054:1` | +2 |
| `company-077:planted-dup-0002:1` | `company-077:bill-premises-00140:1` | +1 |
| `company-077:planted-dup-0003:1` | `company-077:bill-premises-00282:1` | +2 |
| `company-077:planted-dup-0004:1` | `company-077:bill-supplies-00019:1` | +1 |
| `company-077:planted-dup-0005:1` | `company-077:bill-supplies-00089:1` | +1 |
| `company-077:planted-dup-0006:1` | `company-077:bill-supplies-00122:1` | +1 |
| `company-077:planted-dup-0007:1` | `company-077:bill-supplies-00258:1` | +2 |
| `company-077:planted-dup-0008:1` | `company-077:bill-utilities-00256:1` | +3 |
| `company-078:planted-dup-0001:1` | `company-078:bill-insurance-00174:1` | +1 |
| `company-078:planted-dup-0002:1` | `company-078:bill-premises-00259:1` | +2 |
| `company-078:planted-dup-0003:1` | `company-078:bill-supplies-00217:1` | +3 |
| `company-078:planted-dup-0004:1` | `company-078:bill-supplies-00392:1` | +2 |
| `company-078:planted-dup-0005:1` | `company-078:bill-supplies-00521:1` | +1 |
| `company-079:planted-dup-0001:1` | `company-079:bill-income-tax-00094:1` | +2 |
| `company-079:planted-dup-0002:1` | `company-079:bill-premises-00130:1` | +1 |
| `company-079:planted-dup-0003:1` | `company-079:bill-premises-00394:1` | +1 |
| `company-079:planted-dup-0004:1` | `company-079:bill-supplies-00046:1` | +1 |
| `company-079:planted-dup-0005:1` | `company-079:bill-supplies-00070:1` | +1 |
| `company-079:planted-dup-0006:1` | `company-079:bill-supplies-00109:1` | +3 |
| `company-079:planted-dup-0007:1` | `company-079:bill-supplies-00255:1` | +1 |
| `company-079:planted-dup-0008:1` | `company-079:bill-supplies-00328:1` | +1 |
| `company-079:planted-dup-0009:1` | `company-079:bill-supplies-00373:1` | +3 |
| `company-079:planted-dup-0010:1` | `company-079:bill-utilities-00061:1` | +2 |
| `company-079:planted-dup-0011:1` | `company-079:bill-utilities-00193:1` | +3 |
| `company-080:planted-dup-0001:1` | `company-080:bill-insurance-00110:1` | +2 |
| `company-080:planted-dup-0002:1` | `company-080:bill-supplies-00014:1` | +3 |
| `company-080:planted-dup-0003:1` | `company-080:bill-supplies-00248:1` | +1 |
| `company-080:planted-dup-0004:1` | `company-080:bill-supplies-00274:1` | +3 |
| `company-080:planted-dup-0005:1` | `company-080:bill-supplies-00372:1` | +1 |
| `company-080:planted-dup-0006:1` | `company-080:bill-supplies-00385:1` | +1 |
| `company-080:planted-dup-0007:1` | `company-080:bill-supplies-00398:1` | +3 |
| `company-080:planted-dup-0008:1` | `company-080:bill-supplies-00414:1` | +3 |
| `company-080:planted-dup-0009:1` | `company-080:bill-supplies-00453:1` | +3 |
| `company-080:planted-dup-0010:1` | `company-080:bill-supplies-00580:1` | +1 |
| `company-080:planted-dup-0011:1` | `company-080:bill-supplies-00619:1` | +3 |
| `company-080:planted-dup-0012:1` | `company-080:bill-utilities-00288:1` | +3 |
| `company-080:planted-dup-0013:1` | `company-080:bill-utilities-00578:1` | +3 |
| `company-081:planted-dup-0001:1` | `company-081:bill-income-tax-00545:1` | +1 |
| `company-081:planted-dup-0002:1` | `company-081:bill-supplies-00284:1` | +1 |
| `company-081:planted-dup-0003:1` | `company-081:bill-supplies-00371:1` | +1 |
| `company-081:planted-dup-0004:1` | `company-081:bill-supplies-00458:1` | +2 |
| `company-081:planted-dup-0005:1` | `company-081:bill-supplies-00588:1` | +2 |
| `company-081:planted-dup-0006:1` | `company-081:bill-supplies-00892:1` | +1 |
| `company-081:planted-dup-0007:1` | `company-081:bill-utilities-00826:1` | +2 |
| `company-082:planted-dup-0001:1` | `company-082:bill-insurance-00132:1` | +1 |
| `company-082:planted-dup-0002:1` | `company-082:bill-insurance-00365:1` | +3 |
| `company-082:planted-dup-0003:1` | `company-082:bill-premises-00091:1` | +3 |
| `company-082:planted-dup-0004:1` | `company-082:bill-premises-00192:1` | +1 |
| `company-082:planted-dup-0005:1` | `company-082:bill-premises-00325:1` | +1 |
| `company-082:planted-dup-0006:1` | `company-082:bill-supplies-00008:1` | +1 |
| `company-082:planted-dup-0007:1` | `company-082:bill-supplies-00234:1` | +1 |
| `company-082:planted-dup-0008:1` | `company-082:bill-supplies-00311:1` | +3 |
| `company-082:planted-dup-0009:1` | `company-082:bill-utilities-00131:1` | +2 |
| `company-083:planted-dup-0001:1` | `company-083:bill-income-tax-00220:1` | +2 |
| `company-083:planted-dup-0002:1` | `company-083:bill-insurance-00069:1` | +1 |
| `company-083:planted-dup-0003:1` | `company-083:bill-insurance-00121:1` | +2 |
| `company-083:planted-dup-0004:1` | `company-083:bill-insurance-00219:1` | +3 |
| `company-083:planted-dup-0005:1` | `company-083:bill-insurance-00243:1` | +3 |
| `company-083:planted-dup-0006:1` | `company-083:bill-insurance-00271:1` | +2 |
| `company-083:planted-dup-0007:1` | `company-083:bill-premises-00142:1` | +1 |
| `company-083:planted-dup-0008:1` | `company-083:bill-supplies-00109:1` | +1 |
| `company-083:planted-dup-0009:1` | `company-083:bill-supplies-00146:1` | +3 |
| `company-083:planted-dup-0010:1` | `company-083:bill-supplies-00189:1` | +1 |
| `company-083:planted-dup-0011:1` | `company-083:bill-supplies-00221:1` | +3 |
| `company-083:planted-dup-0012:1` | `company-083:bill-utilities-00045:1` | +3 |
| `company-083:planted-dup-0013:1` | `company-083:bill-utilities-00172:1` | +3 |
| `company-084:planted-dup-0001:1` | `company-084:bill-insurance-00332:1` | +3 |
| `company-084:planted-dup-0002:1` | `company-084:bill-premises-00457:1` | +3 |
| `company-084:planted-dup-0003:1` | `company-084:bill-supplies-00097:1` | +2 |
| `company-084:planted-dup-0004:1` | `company-084:bill-supplies-00119:1` | +2 |
| `company-084:planted-dup-0005:1` | `company-084:bill-supplies-00400:1` | +2 |
| `company-084:planted-dup-0006:1` | `company-084:bill-supplies-00430:1` | +1 |
| `company-085:planted-dup-0001:1` | `company-085:bill-insurance-00177:1` | +2 |
| `company-085:planted-dup-0002:1` | `company-085:bill-premises-00092:1` | +3 |
| `company-085:planted-dup-0003:1` | `company-085:bill-premises-00111:1` | +3 |
| `company-085:planted-dup-0004:1` | `company-085:bill-premises-00126:1` | +3 |
| `company-085:planted-dup-0005:1` | `company-085:bill-premises-00175:1` | +2 |
| `company-085:planted-dup-0006:1` | `company-085:bill-supplies-00004:1` | +3 |
| `company-085:planted-dup-0007:1` | `company-085:bill-supplies-00040:1` | +2 |
| `company-085:planted-dup-0008:1` | `company-085:bill-supplies-00129:1` | +3 |
| `company-085:planted-dup-0009:1` | `company-085:bill-utilities-00014:1` | +2 |
| `company-085:planted-dup-0010:1` | `company-085:bill-utilities-00078:1` | +1 |
| `company-086:planted-dup-0001:1` | `company-086:bill-premises-00171:1` | +3 |
| `company-086:planted-dup-0002:1` | `company-086:bill-supplies-00006:1` | +2 |
| `company-086:planted-dup-0003:1` | `company-086:bill-supplies-00039:1` | +2 |
| `company-086:planted-dup-0004:1` | `company-086:bill-supplies-00071:1` | +2 |
| `company-086:planted-dup-0005:1` | `company-086:bill-supplies-00174:1` | +2 |
| `company-086:planted-dup-0006:1` | `company-086:bill-supplies-00179:1` | +1 |
| `company-086:planted-dup-0007:1` | `company-086:bill-supplies-00272:1` | +1 |
| `company-086:planted-dup-0008:1` | `company-086:bill-utilities-00270:1` | +2 |
| `company-087:planted-dup-0001:1` | `company-087:bill-insurance-00065:1` | +2 |
| `company-087:planted-dup-0002:1` | `company-087:bill-insurance-00411:1` | +1 |
| `company-087:planted-dup-0003:1` | `company-087:bill-practice-supplies-00374:1` | +1 |
| `company-087:planted-dup-0004:1` | `company-087:bill-premises-00205:1` | +3 |
| `company-087:planted-dup-0005:1` | `company-087:bill-premises-00239:1` | +3 |
| `company-087:planted-dup-0006:1` | `company-087:bill-premises-00272:1` | +2 |
| `company-088:planted-dup-0001:1` | `company-088:bill-insurance-00064:1` | +2 |
| `company-088:planted-dup-0002:1` | `company-088:bill-insurance-00133:1` | +1 |
| `company-088:planted-dup-0003:1` | `company-088:bill-insurance-00420:1` | +2 |
| `company-088:planted-dup-0004:1` | `company-088:bill-insurance-00501:1` | +3 |
| `company-088:planted-dup-0005:1` | `company-088:bill-insurance-00575:1` | +1 |
| `company-088:planted-dup-0006:1` | `company-088:bill-insurance-00653:1` | +1 |
| `company-088:planted-dup-0007:1` | `company-088:bill-practice-supplies-00208:1` | +1 |
| `company-088:planted-dup-0008:1` | `company-088:bill-practice-supplies-00652:1` | +3 |
| `company-088:planted-dup-0009:1` | `company-088:bill-premises-00288:1` | +2 |
| `company-088:planted-dup-0010:1` | `company-088:bill-premises-00354:1` | +1 |
| `company-088:planted-dup-0011:1` | `company-088:bill-premises-00418:1` | +2 |
| `company-088:planted-dup-0012:1` | `company-088:bill-premises-00573:1` | +1 |
| `company-088:planted-dup-0013:1` | `company-088:bill-premises-00725:1` | +2 |
| `company-088:planted-dup-0014:1` | `company-088:bill-premises-00792:1` | +3 |
| `company-089:planted-dup-0001:1` | `company-089:bill-income-tax-00814:1` | +3 |
| `company-089:planted-dup-0002:1` | `company-089:bill-insurance-00279:1` | +3 |
| `company-089:planted-dup-0003:1` | `company-089:bill-insurance-01510:1` | +1 |
| `company-089:planted-dup-0004:1` | `company-089:bill-practice-supplies-00676:1` | +2 |
| `company-089:planted-dup-0005:1` | `company-089:bill-premises-00675:1` | +1 |
| `company-090:planted-dup-0001:1` | `company-090:bill-insurance-00204:1` | +1 |
| `company-090:planted-dup-0002:1` | `company-090:bill-insurance-00242:1` | +1 |
| `company-090:planted-dup-0003:1` | `company-090:bill-insurance-00349:1` | +3 |
| `company-090:planted-dup-0004:1` | `company-090:bill-insurance-00394:1` | +1 |
| `company-090:planted-dup-0005:1` | `company-090:bill-insurance-00423:1` | +1 |
| `company-090:planted-dup-0006:1` | `company-090:bill-practice-supplies-00033:1` | +2 |
| `company-090:planted-dup-0007:1` | `company-090:bill-practice-supplies-00203:1` | +2 |
| `company-090:planted-dup-0008:1` | `company-090:bill-practice-supplies-00241:1` | +1 |
| `company-090:planted-dup-0009:1` | `company-090:bill-practice-supplies-00275:1` | +3 |
| `company-090:planted-dup-0010:1` | `company-090:bill-practice-supplies-00312:1` | +1 |
| `company-090:planted-dup-0011:1` | `company-090:bill-premises-00421:1` | +1 |
| `company-091:planted-dup-0001:1` | `company-091:bill-insurance-00119:1` | +3 |
| `company-091:planted-dup-0002:1` | `company-091:bill-insurance-00464:1` | +2 |
| `company-091:planted-dup-0003:1` | `company-091:bill-insurance-00582:1` | +3 |
| `company-091:planted-dup-0004:1` | `company-091:bill-practice-supplies-00064:1` | +1 |
| `company-091:planted-dup-0005:1` | `company-091:bill-premises-00063:1` | +3 |
| `company-091:planted-dup-0006:1` | `company-091:bill-premises-00718:1` | +2 |
| `company-092:planted-dup-0001:1` | `company-092:bill-insurance-00237:1` | +1 |
| `company-092:planted-dup-0002:1` | `company-092:bill-insurance-00491:1` | +3 |
| `company-092:planted-dup-0003:1` | `company-092:bill-insurance-00968:1` | +2 |
| `company-092:planted-dup-0004:1` | `company-092:bill-practice-supplies-00608:1` | +2 |
| `company-092:planted-dup-0005:1` | `company-092:bill-practice-supplies-01194:1` | +3 |
| `company-092:planted-dup-0006:1` | `company-092:bill-practice-supplies-01318:1` | +1 |
| `company-092:planted-dup-0007:1` | `company-092:bill-practice-supplies-01421:1` | +2 |
| `company-092:planted-dup-0008:1` | `company-092:bill-premises-00966:1` | +1 |
| `company-092:planted-dup-0009:1` | `company-092:bill-premises-01074:1` | +3 |
| `company-092:planted-dup-0010:1` | `company-092:bill-premises-01317:1` | +3 |
| `company-092:planted-dup-0011:1` | `company-092:bill-premises-01420:1` | +1 |
| `company-093:planted-dup-0001:1` | `company-093:bill-insurance-00103:1` | +3 |
| `company-093:planted-dup-0002:1` | `company-093:bill-insurance-00129:1` | +1 |
| `company-093:planted-dup-0003:1` | `company-093:bill-insurance-00216:1` | +1 |
| `company-093:planted-dup-0004:1` | `company-093:bill-insurance-00242:1` | +3 |
| `company-093:planted-dup-0005:1` | `company-093:bill-practice-supplies-00021:1` | +3 |
| `company-093:planted-dup-0006:1` | `company-093:bill-practice-supplies-00049:1` | +3 |
| `company-093:planted-dup-0007:1` | `company-093:bill-practice-supplies-00069:1` | +1 |
| `company-093:planted-dup-0008:1` | `company-093:bill-practice-supplies-00102:1` | +1 |
| `company-093:planted-dup-0009:1` | `company-093:bill-practice-supplies-00188:1` | +3 |
| `company-093:planted-dup-0010:1` | `company-093:bill-premises-00068:1` | +2 |
| `company-093:planted-dup-0011:1` | `company-093:bill-premises-00187:1` | +3 |
| `company-093:planted-dup-0012:1` | `company-093:bill-premises-00240:1` | +2 |
| `company-093:planted-dup-0013:1` | `company-093:bill-premises-00264:1` | +1 |
| `company-093:planted-dup-0014:1` | `company-093:bill-premises-00331:1` | +3 |
| `company-094:planted-dup-0001:1` | `company-094:bill-insurance-00073:1` | +2 |
| `company-094:planted-dup-0002:1` | `company-094:bill-insurance-00271:1` | +1 |
| `company-094:planted-dup-0003:1` | `company-094:bill-insurance-00382:1` | +3 |
| `company-094:planted-dup-0004:1` | `company-094:bill-practice-supplies-00110:1` | +2 |
| `company-094:planted-dup-0005:1` | `company-094:bill-practice-supplies-00381:1` | +3 |
| `company-094:planted-dup-0006:1` | `company-094:bill-premises-00418:1` | +1 |
| `company-095:planted-dup-0001:1` | `company-095:bill-income-tax-00607:1` | +3 |
| `company-095:planted-dup-0002:1` | `company-095:bill-insurance-00061:1` | +1 |
| `company-095:planted-dup-0003:1` | `company-095:bill-insurance-00126:1` | +1 |
| `company-095:planted-dup-0004:1` | `company-095:bill-insurance-00268:1` | +3 |
| `company-095:planted-dup-0005:1` | `company-095:bill-insurance-00479:1` | +3 |
| `company-095:planted-dup-0006:1` | `company-095:bill-insurance-00541:1` | +1 |
| `company-095:planted-dup-0007:1` | `company-095:bill-insurance-00606:1` | +3 |
| `company-095:planted-dup-0008:1` | `company-095:bill-insurance-00671:1` | +1 |
| `company-095:planted-dup-0009:1` | `company-095:bill-insurance-00808:1` | +1 |
| `company-095:planted-dup-0010:1` | `company-095:bill-practice-supplies-00125:1` | +3 |
| `company-095:planted-dup-0011:1` | `company-095:bill-premises-00187:1` | +2 |
| `company-095:planted-dup-0012:1` | `company-095:bill-premises-00397:1` | +1 |
| `company-095:planted-dup-0013:1` | `company-095:bill-premises-00669:1` | +1 |
| `company-096:planted-dup-0001:1` | `company-096:bill-income-tax-00040:1` | +3 |
| `company-096:planted-dup-0002:1` | `company-096:bill-income-tax-00126:1` | +2 |
| `company-096:planted-dup-0003:1` | `company-096:bill-insurance-00014:1` | +2 |
| `company-096:planted-dup-0004:1` | `company-096:bill-insurance-00054:1` | +2 |
| `company-096:planted-dup-0005:1` | `company-096:bill-insurance-00097:1` | +2 |
| `company-096:planted-dup-0006:1` | `company-096:bill-insurance-00111:1` | +3 |
| `company-096:planted-dup-0007:1` | `company-096:bill-insurance-00138:1` | +3 |
| `company-096:planted-dup-0008:1` | `company-096:bill-insurance-00169:1` | +2 |
| `company-096:planted-dup-0009:1` | `company-096:bill-practice-supplies-00024:1` | +1 |
| `company-096:planted-dup-0010:1` | `company-096:bill-practice-supplies-00096:1` | +1 |
| `company-096:planted-dup-0011:1` | `company-096:bill-practice-supplies-00168:1` | +1 |
| `company-096:planted-dup-0012:1` | `company-096:bill-premises-00052:1` | +3 |
| `company-096:planted-dup-0013:1` | `company-096:bill-premises-00136:1` | +1 |
| `company-096:planted-dup-0014:1` | `company-096:bill-premises-00152:1` | +2 |
| `company-096:planted-dup-0015:1` | `company-096:bill-premises-00167:1` | +3 |
| `company-097:planted-dup-0001:1` | `company-097:bill-insurance-00069:1` | +2 |
| `company-097:planted-dup-0002:1` | `company-097:bill-premises-00045:1` | +2 |
| `company-097:planted-dup-0003:1` | `company-097:bill-premises-00067:1` | +2 |
| `company-097:planted-dup-0004:1` | `company-097:bill-premises-00104:1` | +1 |
| `company-097:planted-dup-0005:1` | `company-097:bill-premises-00115:1` | +2 |
| `company-097:planted-dup-0006:1` | `company-097:bill-premises-00139:1` | +2 |
| `company-097:planted-dup-0007:1` | `company-097:bill-program-services-00068:1` | +3 |
| `company-097:planted-dup-0008:1` | `company-097:bill-program-services-00140:1` | +2 |
| `company-098:planted-dup-0001:1` | `company-098:bill-insurance-00019:1` | +1 |
| `company-098:planted-dup-0002:1` | `company-098:bill-insurance-00081:1` | +3 |
| `company-098:planted-dup-0003:1` | `company-098:bill-insurance-00183:1` | +2 |
| `company-098:planted-dup-0004:1` | `company-098:bill-premises-00017:1` | +2 |
| `company-098:planted-dup-0005:1` | `company-098:bill-premises-00036:1` | +3 |
| `company-098:planted-dup-0006:1` | `company-098:bill-premises-00055:1` | +3 |
| `company-098:planted-dup-0007:1` | `company-098:bill-premises-00079:1` | +2 |
| `company-098:planted-dup-0008:1` | `company-098:bill-premises-00098:1` | +2 |
| `company-098:planted-dup-0009:1` | `company-098:bill-premises-00141:1` | +1 |
| `company-098:planted-dup-0010:1` | `company-098:bill-premises-00200:1` | +2 |
| `company-098:planted-dup-0011:1` | `company-098:bill-program-services-00018:1` | +1 |
| `company-098:planted-dup-0012:1` | `company-098:bill-program-services-00162:1` | +1 |
| `company-098:planted-dup-0013:1` | `company-098:bill-program-services-00182:1` | +3 |
| `company-099:planted-dup-0001:1` | `company-099:bill-insurance-00247:1` | +2 |
| `company-099:planted-dup-0002:1` | `company-099:bill-insurance-00599:1` | +3 |
| `company-099:planted-dup-0003:1` | `company-099:bill-premises-00139:1` | +1 |
| `company-099:planted-dup-0004:1` | `company-099:bill-premises-00245:1` | +1 |
| `company-099:planted-dup-0005:1` | `company-099:bill-premises-00293:1` | +2 |
| `company-099:planted-dup-0006:1` | `company-099:bill-program-services-00046:1` | +1 |
| `company-099:planted-dup-0007:1` | `company-099:bill-program-services-00294:1` | +2 |
| `company-099:planted-dup-0008:1` | `company-099:bill-program-services-00352:1` | +3 |
| `company-099:planted-dup-0009:1` | `company-099:bill-program-services-00399:1` | +1 |
| `company-099:planted-dup-0010:1` | `company-099:bill-program-services-00446:1` | +2 |
| `company-100:planted-dup-0001:1` | `company-100:bill-insurance-00058:1` | +3 |
| `company-100:planted-dup-0002:1` | `company-100:bill-insurance-00070:1` | +3 |
| `company-100:planted-dup-0003:1` | `company-100:bill-premises-00081:1` | +3 |
| `company-100:planted-dup-0004:1` | `company-100:bill-premises-00092:1` | +2 |
| `company-100:planted-dup-0005:1` | `company-100:bill-premises-00138:1` | +1 |
| `company-100:planted-dup-0006:1` | `company-100:bill-program-services-00057:1` | +1 |
| `company-100:planted-dup-0007:1` | `company-100:bill-program-services-00115:1` | +2 |

<!-- END GENERATED -->
