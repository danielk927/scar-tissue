# Dataset (P1-01)

**The dataset has 100 companies, so the Section 9.1 held-out design (D-07) stands.**

Every number below comes from read-only scripts run over the extracted files.
P1-02's loader tests should re-assert the key ones (row counts, the P&L reconciliation in "Mapping to canonical schema", and the split).

## Source and files

- Repo: `CL251215/QuickBooks-Hackathon-2026` (Hugging Face dataset), revision `dc68d478a190c879f26fd94b764298c2c6308fa7`, last modified 2026-10-10.
- The repo has no dataset card and two files: `.gitattributes` (2,504 bytes) and `hackathon-100-participant.tar.gz` (26,675,707 bytes, LFS).
- Local copy: `/Users/doyoonkim/scar-data/quickbooks-hackathon-2026/` (tarball), extracted to `/Users/doyoonkim/scar-data/quickbooks-hackathon-2026/extracted/hackathon-100/`.
- The tarball has 2,703 members: 2,302 files (2,200 `.yaml`, 101 `.md`, 1 `.json`) plus directories, 414,307,293 bytes uncompressed.
- It has no symlinks, absolute paths, `..` paths, or scripts; it was extracted with Python `tarfile` `filter="data"`.
- Generator: qb-world-sim, "realized world model" schema v4, `realize_version` 6, currency USD in all 100 manifests.
- `DATA_README.md` mentions a `hidden/` directory (personas, market context), but the participant tarball does not contain it (0 found).

## Organization

```
hackathon-100/
  DATA_README.md
  index.json                         100 entries: company_id, display_name, industry, profile,
                                     size_tier, dataset_id, status, financials_cents, entity_counts
  company-001/rwm-v4/world-<16 hex>/ one directory per company
    company.yaml accounts.yaml vendors.yaml customers.yaml employees.yaml items.yaml
    bills.yaml bill_payments.yaml invoices.yaml sales_receipts.yaml customer_payments.yaml
    journal_entries.yaml bank_transactions.yaml payroll_runs.yaml projects.yaml
    estimates.yaml terms.yaml manifest.yaml SCHEMA.md
    reports/profit_and_loss.yaml balance_sheet.yaml ar_aging.yaml ap_aging.yaml
  ...
  company-100/...
```

- One directory per company, one YAML file per entity type; there is no company column inside the files, so company identity comes from the directory.
- Each company directory holds exactly one `world-<id>` directory, equal to its `dataset_id` in `index.json`.
- Every entity file is a top-level YAML list of mappings; `manifest.yaml` and the four reports are single mappings.
- `SCHEMA.md` is byte-identical in all 100 directories (md5 `b3cb4b24494b484b2a39eacd382caa31`) and is the full field reference.
- `index.json` `entity_counts` match the record counts in the files for every company (0 mismatches).
- The books are double-entry: commercial documents (bills, invoices, sales receipts, payments) plus journal entries for everything else.
- `bank_transactions` and `payroll_runs` are derived views of those books, not independent postings.

## Companies

- 100 companies with 100 distinct names and 71 distinct industries (NAICS titles).
- Profiles: wholesale 22, construction 22, recurring 16, pos 14, mixed 12, appointments 10, nonprofit 4.
- Size tiers: small 37, medium 32, large 31; `distressed: true` for 7 companies.
- Every company opens on 2025-10-06 with an owner contribution, has a January fiscal year start, and covers 365 simulated days.
- Overall raw date range: 2025-10-06 to 2026-10-06 (last document on 2026-10-06 for 84 companies, 2026-10-05 for 16).
- Proposed canonical `ledger.transactions` rows per company: min 92, median 704, mean 1,597.9, max 11,601, total 159,790.
- Canonical first transaction dates vary (2025-10-06 to 2025-10-13) because the opening-equity entry is not a canonical transaction; last dates are 2026-10-06 (56 companies) or 2026-10-05 (44).

Column notes: `txn rows` is the proposed canonical `ledger.transactions` row count (bills plus expense journal lines, see below); `bills` is the raw bill count; `first txn` and `last txn` are the canonical date range.

| company_id | name | industry | profile | txn rows | bills | vendors | first txn | last txn | split |
|---|---|---|---|---:|---:|---:|---|---|---|
| company-001 | Hearthloaf Bakeries | Commercial Bakeries | wholesale | 2,149 | 359 | 7 | 2025-10-10 | 2026-10-06 | eval |
| company-002 | Roastline Coffee Roasters | Coffee and Tea Manufacturing | wholesale | 3,841 | 349 | 7 | 2025-10-10 | 2026-10-06 | eval |
| company-003 | Keghouse Brewing Co | Breweries | wholesale | 6,336 | 377 | 8 | 2025-10-09 | 2026-10-06 | eval |
| company-004 | Stitchform Apparel Co | Cut and Sew Apparel Manufacturing | wholesale | 2,475 | 358 | 8 | 2025-10-09 | 2026-10-06 | train |
| company-005 | Pistonhouse Auto Parts | Motor Vehicle Supplies and New Parts Merchant Wholesalers | wholesale | 3,931 | 370 | 10 | 2025-10-10 | 2026-10-06 | train |
| company-006 | Knotwood Lumber Supply | Lumber, Plywood, Millwork, and Wood Panel Merchant Wholesalers | wholesale | 6,481 | 234 | 9 | 2025-10-13 | 2026-10-06 | train |
| company-007 | Scrubline Medical Supply | Medical, Dental, and Hospital Equipment and Supplies Merchant Wholesalers | wholesale | 2,156 | 226 | 8 | 2025-10-13 | 2026-10-06 | train |
| company-008 | Conduitworks Electrical Supply | Electrical Apparatus and Equipment, Wiring Supplies, and Related Equipment Merchant Wholesalers | wholesale | 3,748 | 231 | 8 | 2025-10-13 | 2026-10-06 | train |
| company-009 | Soldertown Plumbing Supply | Plumbing and Heating Equipment and Supplies (Hydronics) Merchant Wholesalers | wholesale | 6,151 | 230 | 10 | 2025-10-13 | 2026-10-06 | train |
| company-010 | Driveline Machinery Supply | Industrial Machinery and Equipment Merchant Wholesalers | wholesale | 3,906 | 1,804 | 9 | 2025-10-13 | 2026-10-06 | train |
| company-011 | Grindell Industrial Supply | Industrial Supplies Merchant Wholesalers | wholesale | 6,519 | 3,021 | 10 | 2025-10-13 | 2026-10-06 | eval |
| company-012 | Baseline Sporting Goods | Sporting and Recreational Goods and Supplies Merchant Wholesalers | wholesale | 11,601 | 5,461 | 9 | 2025-10-13 | 2026-10-06 | train |
| company-013 | Ledgermark Office Supply | Stationery and Office Supplies Merchant Wholesalers | wholesale | 2,361 | 211 | 7 | 2025-10-13 | 2026-10-06 | train |
| company-014 | Rollforth Paper Supply | Industrial and Personal Service Paper Merchant Wholesalers | wholesale | 3,211 | 233 | 8 | 2025-10-13 | 2026-10-06 | train |
| company-015 | Harvest Line Food Distributors | General Line Grocery Merchant Wholesalers | wholesale | 6,130 | 234 | 8 | 2025-10-13 | 2026-10-06 | eval |
| company-016 | Coppervat Beverage Supply | Beer and Ale Merchant Wholesalers | wholesale | 3,869 | 1,821 | 10 | 2025-10-13 | 2026-10-06 | train |
| company-017 | Trinketvale Novelty Distributors | Other Miscellaneous Nondurable Goods Merchant Wholesalers | wholesale | 7,145 | 3,330 | 10 | 2025-10-13 | 2026-10-06 | eval |
| company-018 | Shipwise Online Goods | Electronic Shopping and Mail-Order Houses | wholesale | 6,602 | 416 | 9 | 2025-10-10 | 2026-10-06 | train |
| company-019 | Pawmart Pet Supply | Pet and Pet Supplies Retailers | wholesale | 2,276 | 333 | 8 | 2025-10-08 | 2026-10-06 | train |
| company-020 | Palletbay Storage & Warehousing | General Warehousing and Storage | wholesale | 4,006 | 333 | 8 | 2025-10-09 | 2026-10-06 | train |
| company-021 | Crumbford Commercial Bakery | Commercial Bakeries | wholesale | 6,517 | 415 | 8 | 2025-10-09 | 2026-10-06 | train |
| company-022 | Emberpot Coffee Roasters | Coffee and Tea Manufacturing | wholesale | 504 | 82 | 7 | 2025-10-08 | 2026-10-06 | eval |
| company-023 | Reelmark Productions | Motion Picture and Video Production | construction | 452 | 382 | 13 | 2025-10-06 | 2026-10-06 | eval |
| company-024 | Trueline Architecture | Architectural Services | construction | 709 | 625 | 13 | 2025-10-06 | 2026-10-06 | train |
| company-025 | Umber Room Interiors | Interior Design Services | construction | 880 | 785 | 13 | 2025-10-06 | 2026-10-06 | eval |
| company-026 | Loudfield Advertising | Advertising Agencies | construction | 484 | 419 | 12 | 2025-10-06 | 2026-10-06 | eval |
| company-027 | Saffron Table Catering | Caterers | construction | 1,845 | 610 | 15 | 2025-10-06 | 2026-10-06 | eval |
| company-028 | Gilded Oak Weddings | All Other Personal Services | construction | 2,618 | 842 | 15 | 2025-10-06 | 2026-10-06 | train |
| company-029 | Birchfield Custom Homes | New Single-Family Housing Construction (except For-Sale Builders) | construction | 1,533 | 553 | 17 | 2025-10-06 | 2026-10-06 | train |
| company-030 | Hearthworn Remodeling | Residential Remodelers | construction | 1,815 | 596 | 16 | 2025-10-06 | 2026-10-06 | train |
| company-031 | Keystone Commercial Construction | Commercial and Institutional Building Construction | construction | 699 | 611 | 12 | 2025-10-06 | 2026-10-06 | train |
| company-032 | Clapboard Lane Films | Motion Picture and Video Production | construction | 350 | 282 | 11 | 2025-10-13 | 2026-10-06 | train |
| company-033 | Datum Point Architects | Architectural Services | construction | 655 | 576 | 12 | 2025-10-13 | 2026-10-06 | eval |
| company-034 | Soft Palette Interiors | Interior Design Services | construction | 540 | 465 | 12 | 2025-10-06 | 2026-10-06 | eval |
| company-035 | Brightmark Ad Agency | Advertising Agencies | construction | 475 | 407 | 13 | 2025-10-06 | 2026-10-06 | train |
| company-036 | Greenverge Landscaping | Landscaping Services | construction | 446 | 381 | 12 | 2025-10-13 | 2026-10-06 | train |
| company-037 | Copper Ladle Catering | Caterers | construction | 1,639 | 358 | 13 | 2025-10-06 | 2026-10-06 | train |
| company-038 | Confetti Row Events | All Other Personal Services | construction | 1,031 | 386 | 14 | 2025-10-06 | 2026-10-06 | train |
| company-039 | Lanterncrest Home Construction | New Single-Family Housing Construction (except For-Sale Builders) | construction | 1,587 | 383 | 15 | 2025-10-06 | 2026-10-06 | eval |
| company-040 | Courtyard Commons Apartments Construction | New Multifamily Housing Construction (except For-Sale Builders) | construction | 2,258 | 601 | 14 | 2025-10-06 | 2026-10-06 | train |
| company-041 | Clovermead Homes | New Housing For-Sale Builders | construction | 1,122 | 320 | 14 | 2025-10-06 | 2026-10-06 | train |
| company-042 | Sawhorse Home Remodelers | Residential Remodelers | construction | 576 | 502 | 13 | 2025-10-13 | 2026-10-06 | train |
| company-043 | Granary Commercial Builders | Commercial and Institutional Building Construction | construction | 843 | 749 | 13 | 2025-10-06 | 2026-10-06 | train |
| company-044 | Lumenwork Video | Motion Picture and Video Production | construction | 454 | 388 | 12 | 2025-10-06 | 2026-10-06 | train |
| company-045 | Sunward Roof & Solar | Roofing Contractors | mixed | 773 | 451 | 13 | 2025-10-06 | 2026-10-06 | eval |
| company-046 | Brightline Electric & Supply | Electrical Contractors | mixed | 1,318 | 535 | 13 | 2025-10-06 | 2026-10-06 | train |
| company-047 | Ductline Mechanical & Supply | Plumbing, Heating, and Air-Conditioning Contractors | mixed | 1,867 | 549 | 14 | 2025-10-06 | 2026-10-06 | train |
| company-048 | Guardwire Security Systems | Other Building Equipment Contractors | mixed | 1,089 | 473 | 14 | 2025-10-06 | 2026-10-06 | eval |
| company-049 | Jointer Finish Carpentry | Finish Carpentry Contractors | mixed | 1,384 | 343 | 11 | 2025-10-06 | 2026-10-06 | eval |
| company-050 | Plasterline Finishing & Supply | Other Building Finishing Contractors | mixed | 2,064 | 388 | 13 | 2025-10-06 | 2026-10-06 | eval |
| company-051 | Oddjob Specialty Trades & Supply | All Other Specialty Trade Contractors | mixed | 1,362 | 429 | 14 | 2025-10-06 | 2026-10-06 | train |
| company-052 | Maple & Stone Kitchens | Wood Kitchen Cabinet and Countertop Manufacturing | mixed | 1,742 | 505 | 13 | 2025-10-06 | 2026-10-06 | eval |
| company-053 | Brightface Sign Co | Sign Manufacturing | mixed | 2,288 | 462 | 13 | 2025-10-06 | 2026-10-06 | train |
| company-054 | Signalcraft Systems Integration | Computer and Computer Peripheral Equipment and Software Merchant Wholesalers | mixed | 1,418 | 426 | 13 | 2025-10-06 | 2026-10-06 | train |
| company-055 | Peakline Roof & Solar | Roofing Contractors | mixed | 1,747 | 473 | 12 | 2025-10-06 | 2026-10-06 | train |
| company-056 | Voltpost Electrical Contractors | Electrical Contractors | mixed | 2,304 | 424 | 11 | 2025-10-06 | 2026-10-06 | train |
| company-057 | Ledgerline Bookkeeping | Other Accounting Services | recurring | 92 | 40 | 4 | 2025-10-13 | 2026-10-05 | train |
| company-058 | Beacon Hill Tax & Accounting | Tax Preparation Services | recurring | 196 | 40 | 4 | 2025-10-13 | 2026-10-05 | train |
| company-059 | Stackwise Managed IT | Computer Systems Design Services | recurring | 352 | 40 | 4 | 2025-10-13 | 2026-10-05 | train |
| company-060 | Fernwood Payroll Services | Payroll Services | recurring | 92 | 40 | 4 | 2025-10-13 | 2026-10-05 | train |
| company-061 | Clearbook CPA Group | Offices of Certified Public Accountants | recurring | 196 | 40 | 4 | 2025-10-13 | 2026-10-05 | train |
| company-062 | Ironbark Fitness Club | Fitness and Recreational Sports Centers | recurring | 352 | 40 | 4 | 2025-10-13 | 2026-10-05 | eval |
| company-063 | Helm & Harbor PR Retainers | Public Relations Agencies | recurring | 92 | 40 | 4 | 2025-10-13 | 2026-10-05 | train |
| company-064 | Stonebriar Residential Properties | Lessors of Residential Buildings and Dwellings | recurring | 144 | 40 | 4 | 2025-10-13 | 2026-10-05 | train |
| company-065 | Oakmoor Property Management | Residential Property Managers | recurring | 248 | 40 | 4 | 2025-10-13 | 2026-10-05 | train |
| company-066 | Garnet Row Apartments | Lessors of Residential Buildings and Dwellings | recurring | 92 | 40 | 4 | 2025-10-13 | 2026-10-05 | train |
| company-067 | Lanternwade Commercial Realty | Lessors of Nonresidential Buildings | recurring | 144 | 40 | 4 | 2025-10-13 | 2026-10-05 | train |
| company-068 | Willowmere Lofts | Lessors of Residential Buildings and Dwellings | recurring | 248 | 40 | 4 | 2025-10-13 | 2026-10-05 | train |
| company-069 | Meridian Wealth Advisors | Investment Advice | recurring | 144 | 40 | 4 | 2025-10-13 | 2026-10-05 | train |
| company-070 | Larchmont Capital Counsel | Investment Advice | recurring | 248 | 40 | 4 | 2025-10-13 | 2026-10-05 | train |
| company-071 | Northgate Portfolio Partners | Portfolio Management | recurring | 456 | 40 | 4 | 2025-10-13 | 2026-10-05 | train |
| company-072 | Silverbirch Financial Planning | Investment Advice | recurring | 140 | 36 | 3 | 2025-10-13 | 2026-10-05 | train |
| company-073 | Copper Kettle Cafe | Snack and Nonalcoholic Beverage Bars | pos | 248 | 92 | 5 | 2025-10-13 | 2026-10-05 | train |
| company-074 | Driftwood Espresso Bar | Snack and Nonalcoholic Beverage Bars | pos | 404 | 92 | 5 | 2025-10-13 | 2026-10-05 | eval |
| company-075 | Morning Lark Bakeshop | Retail Bakeries | pos | 716 | 92 | 5 | 2025-10-13 | 2026-10-05 | train |
| company-076 | Juniper & Rye Coffeehouse | Snack and Nonalcoholic Beverage Bars | pos | 246 | 90 | 5 | 2025-10-13 | 2026-10-05 | train |
| company-077 | Sugarpine Donut Co. | Retail Bakeries | pos | 402 | 90 | 5 | 2025-10-13 | 2026-10-05 | eval |
| company-078 | Little Harvest Juice Bar | Snack and Nonalcoholic Beverage Bars | pos | 1,184 | 92 | 5 | 2025-10-13 | 2026-10-05 | train |
| company-079 | Tavola Rossa Trattoria | Full-Service Restaurants | pos | 456 | 92 | 5 | 2025-10-13 | 2026-10-05 | eval |
| company-080 | Blue Heron Bistro | Full-Service Restaurants | pos | 768 | 92 | 5 | 2025-10-13 | 2026-10-05 | eval |
| company-081 | Smokestack Barbecue House | Full-Service Restaurants | pos | 1,184 | 92 | 5 | 2025-10-13 | 2026-10-05 | train |
| company-082 | Lotus Garden Kitchen | Full-Service Restaurants | pos | 456 | 92 | 5 | 2025-10-13 | 2026-10-05 | eval |
| company-083 | El Farolito Taqueria | Limited-Service Restaurants | pos | 352 | 92 | 5 | 2025-10-13 | 2026-10-05 | eval |
| company-084 | Pagewood Books & Prints | Book Retailers and News Dealers | pos | 560 | 92 | 5 | 2025-10-13 | 2026-10-05 | train |
| company-085 | Thistle & Thread Yarn Shop | Sewing, Needlework, and Piece Goods Retailers | pos | 248 | 92 | 5 | 2025-10-13 | 2026-10-05 | eval |
| company-086 | Rolling Spoke Bicycle Shop | Sporting Goods Retailers | pos | 352 | 92 | 5 | 2025-10-13 | 2026-10-05 | train |
| company-087 | Lakeview Family Dental | Offices of Dentists | appointments | 192 | 36 | 3 | 2025-10-13 | 2026-10-05 | train |
| company-088 | Birchwood Orthodontics | Offices of Dentists | appointments | 351 | 39 | 4 | 2025-10-13 | 2026-10-05 | train |
| company-089 | Cedar Hollow Veterinary Clinic | Veterinary Services | appointments | 716 | 40 | 4 | 2025-10-13 | 2026-10-05 | train |
| company-090 | Elm Street Counseling Group | Offices of Mental Health Practitioners | appointments | 192 | 36 | 3 | 2025-10-13 | 2026-10-05 | train |
| company-091 | Quietwater Counsel LLP | Offices of Lawyers | appointments | 300 | 40 | 4 | 2025-10-13 | 2026-10-05 | train |
| company-092 | Ashgrove Family Law | Offices of Lawyers | appointments | 611 | 39 | 4 | 2025-10-13 | 2026-10-05 | train |
| company-093 | Hollis & Pine Legal Group | Offices of Lawyers | appointments | 142 | 38 | 4 | 2025-10-13 | 2026-10-05 | train |
| company-094 | Velvet Finch Salon | Beauty Salons | appointments | 300 | 40 | 4 | 2025-10-13 | 2026-10-05 | eval |
| company-095 | Clipper & Crown Barbershop | Barber Shops | appointments | 612 | 40 | 4 | 2025-10-13 | 2026-10-05 | eval |
| company-096 | Golden Hour Massage Studio | Other Personal Care Services | appointments | 144 | 40 | 4 | 2025-10-13 | 2026-10-05 | train |
| company-097 | Brightwater Community Fund | Civic and Social Organizations | nonprofit | 141 | 37 | 4 | 2025-10-13 | 2026-10-05 | train |
| company-098 | Open Table Food Pantry | Community Food Services | nonprofit | 245 | 37 | 4 | 2025-10-13 | 2026-10-05 | train |
| company-099 | Riverstage Arts Alliance | Promoters of Performing Arts with Facilities | nonprofit | 609 | 37 | 4 | 2025-10-13 | 2026-10-05 | train |
| company-100 | Lanternlight Community Theater | Promoters of Performing Arts with Facilities | nonprofit | 141 | 37 | 4 | 2025-10-13 | 2026-10-05 | eval |

## Raw schema per file

All money fields are two-decimal strings (`"1234.56"`), all dates are `YYYY-MM-DD` strings, and all IDs are strings.
Fields marked `?` are nullable.
`SCHEMA.md` has every field; the table lists the fields that matter for us.

| file | records (all companies) | per company min / median / max | companies with 0 | key fields (type) |
|---|---:|---|---:|---|
| `company.yaml` | 100 | 1 / 1 / 1 | 0 | `id` (always `company-main`), `legal_name`, `trade_name`, `industry_naics`, `industry_title`, `opening_date`, `fiscal_year_start_month` (int), `address` |
| `accounts.yaml` | 3,700 | 34 / 37 / 38 | 0 | `id`, `name`, `classification` (asset, liability, equity, revenue, expense), `account_type` (QBO-style, e.g. `bank`), `account_subtype`, `description` |
| `vendors.yaml` | 821 | 3 / 8 / 17 | 0 | `id`, `display_name`, `company_name`, `supply_role`, `payment_terms`, `payment_terms_days` (int), `joined_date` |
| `customers.yaml` | 8,322 | 1 / 19.5 / 1,526 | 0 | `id`, `display_name`, terms, `credit_limit_amount`, `joined_date`, `ended_date?` |
| `employees.yaml` | 582 | 1 / 4 / 20 | 0 | `id`, `display_name`, `job_title`, `weekly_wage_amount`, `hired_date`, `released_date?` |
| `items.yaml` | 384 | 1 / 3.5 / 8 | 0 | `id`, `name`, `item_type`, `unit_price_amount`, `unit_cost_amount`, `income_account_id`, `purchase_account_id` |
| `bills.yaml` | 39,563 | 36 / 233.5 / 5,461 | 0 | `id`, `doc_number`, `vendor_id`, `vendor_name`, `expense_kind` (product, operating), `txn_date`, `due_date`, `item_lines[]` {`line_num`, `item_id`, `quantity`, `amount`}, `account_lines[]` {`line_num`, `account_id`, `description`, `amount`}, `total_amount`, `project_id?`, `cost_category`, `memo` |
| `bill_payments.yaml` | 36,032 | 25 / 225 / 4,928 | 0 | `id`, `vendor_id`, `txn_date`, `amount`, `applications[]` {`document_id`, `amount`}, `payment_account_id` |
| `invoices.yaml` | 84,451 | 0 / 187.5 / 5,551 | 14 | `id`, `customer_id`, `txn_date`, `due_date`, `lines[]`, `total_amount`, `income_account_id`, `project_id?`, `deposit_applied_amount`, `retainage_amount` |
| `sales_receipts.yaml` | 28,501 | 0 / 0 / 7,366 | 72 | `id`, `customer_id`, `txn_date`, `lines[]`, `total_amount`, `income_account_id`, `deposit_account_id` |
| `customer_payments.yaml` | 143,616 | 0 / 171 / 10,501 | 16 | `id`, `customer_id`, `txn_date`, `amount`, `applications[]`, `unapplied_amount`, `settles`, `source_account_id`, `deposit_account_id` |
| `journal_entries.yaml` | 151,730 | 107 / 927.5 / 6,822 | 0 | `id`, `doc_number`, `purpose` (enum), `txn_date`, `lines[]` {`line_num`, `account_id`, `description`, `debit_amount`, `credit_amount`, `customer_id?`, `vendor_id?`, `document_id?`}, `project_id?`, `memo` |
| `bank_transactions.yaml` | 234,610 | 39 / 1,019 / 14,266 | 0 | `id`, `date`, `amount` (signed), `direction` (debit, credit), `running_balance_amount`, `source_document_id`, `memo`, `kind` |
| `payroll_runs.yaml` | 25,733 | 4 / 224 / 1,020 | 0 | `id`, `date`, `total_amount`, `gross_only` (bool), `lines[]` {`employee_id`, `gross_amount`}, `source_journal_entry_id` |
| `projects.yaml` | 203 | 0 / 0 / 14 | 66 | `id`, `name`, `customer_id`, `status`, contract amounts, change orders |
| `estimates.yaml` | 299 | 0 / 0 / 30 | 76 | `id`, `date`, `amount`, `status` (won, lost, pending) |
| `terms.yaml` | 176 | 1 / 2 / 3 | 0 | `id`, `name`, `days` (int) |
| `manifest.yaml` | mapping | - | - | `dataset_id`, `currency`, `anchor_date`, `sim.seed`, `sim.days`, `sim.config` (simulation parameters) |
| `reports/*.yaml` | mapping | - | - | P&L (`full_period.lines`, `monthly`), balance sheet, AR aging, AP aging |

Structural facts checked in the data:

- Every bill has exactly one line: 37,474 bills have one `account_lines` entry, 2,089 product bills have one `item_lines` entry, and the line amount always equals `total_amount`.
- Every bill payment has exactly one application and pays from `acct-checking`; 36,032 bills are fully paid, 3,531 are unpaid, and none are partially paid.
- Journal entries: 151,728 have 2 lines, 1 has 5 lines and 1 has 6 lines (both `client_default` in company-044); every entry balances.
- Journal entry lines never carry a `vendor_id` (0 of 303,467); 46 carry a `customer_id` and 53 a `document_id` (bad debt and client default only).
- JE `purpose` counts: cost_of_goods 75,863, wage_accrual 26,705, wage_payment 25,733, interest_accrual 7,196, depreciation 5,923, card_settlement 4,576, wip_capitalization 4,077, wip_release 859, financing_draw 273, deposit_receipt 190, owner_distribution 152, opening_equity 100, bad_debt 41, financing_repayment 32, client_default 7, inventory_disposal 3.

## Mapping to canonical schema

### `ledger.companies`

| canonical | raw source |
|---|---|
| `company_id` | `index.json` `company_id` (`company-001` ... `company-100`, same as the directory name). `company.yaml` `id` is `company-main` in all 100 files and cannot be used. |
| `name` | `index.json` `display_name` (equal to `company.yaml` `trade_name` for all 100; `legal_name` adds a suffix such as "LLC") |
| `industry` | `index.json` `industry_title` (equal to `company.yaml` `industry_title`; NAICS code is in `industry_naics`) |
| `split` | computed per Section 9.1 (see "Split") |

### `ledger.accounts`

| canonical | raw source |
|---|---|
| `account_id` | `{company_id}:{accounts.yaml id}`, e.g. `company-001:acct-rent` |
| `company_id` | directory |
| `name` | `name` (unique within a company) |
| `account_type` | `classification`, mapped as below; the raw field named `account_type` is a finer QBO-style type and must not be copied into canonical `account_type` |

| raw `classification` | raw `account_type` values (rows) | canonical `account_type` |
|---|---|---|
| `asset` | `bank` 100, `accounts_receivable` 100, `other_current_asset` 400, `fixed_asset` 200 | `asset` |
| `liability` | `accounts_payable` 100, `credit_card` 100, `other_current_liability` 396, `long_term_liability` 100 | `liability` |
| `equity` | `equity` 196 | `equity` |
| `revenue` | `income` 112 | `income` |
| `expense` | `expense` 1,300, `cost_of_goods_sold` 400, `other_expense` 196 | `expense` |

- 38 distinct raw account ids; each company has 37 (84 companies), 38 (12) or 34 (4) accounts.
- The 12 companies with 38 accounts have both `acct-product-income` and `acct-construction-income`; the 4 nonprofits with 34 lack `acct-owner-distributions`, `acct-tax-payable` and `acct-income-tax`.
- `acct-product-income` appears in 78 companies under 22 different industry names (e.g. "Bakery Sales", "Advisory Fees"); all other ids have one name everywhere.

### `ledger.vendors`

| canonical | raw source |
|---|---|
| `vendor_id` | `{company_id}:{vendors.yaml id}`, e.g. `company-001:vend-landlord` |
| `company_id` | directory |
| `name` | `display_name` (equal to `company_name` for all 821; unique within a company) |

- A real vendor table exists, so nothing needs to be derived from descriptions.
- 821 vendors, 3 to 17 per company, 30 distinct raw ids; every vendor has at least one bill.
- `supply_role` counts: subcontract 219, operating 145, government 108, utility 104, premises 100, insurance 66, product 48, lender 31.

### `ledger.transactions`

Proposed scope: one canonical row per **bill line** plus one per **journal entry line on an expense-classified account**.
Nothing else is loaded (see "Rows that don't fit").
This gives 159,790 rows: 39,563 from bills and 120,227 from journal lines.

From `bills.yaml` (one row per line; every bill has exactly one line):

| canonical | raw source |
|---|---|
| `txn_id` | `{company_id}:{bill id}:{line_num}`, e.g. `company-002:bill-00008:1` |
| `company_id` | directory |
| `txn_date` | bill `txn_date` |
| `amount` | line `amount` (always > 0) |
| `account_id` | `account_lines[].account_id`, or for `item_lines` the item's `purchase_account_id` (always `acct-inventory`), prefixed with `{company_id}:` |
| `vendor_id` | bill `vendor_id`, prefixed |
| `description` | line `description` (e.g. "Outbound carriage on customer shipments (freight)." or the item description) |
| `txn_type` | `bill` |

Do not use the bill `memo` for `description`: it comes from 20 fixed strings and is often unrelated to the bill (e.g. "Partial backorder noted; balance to follow separately." appears 4,328 times, including 378 landlord, 84 insurer and 269 `vend-electricity` bills).

From `journal_entries.yaml` (one row per line whose account has `classification: expense`):

| canonical | raw source |
|---|---|
| `txn_id` | `{company_id}:{je id}:{line_num}`, e.g. `company-002:je-cogs-00247:1` |
| `company_id` | directory |
| `txn_date` | entry `txn_date` |
| `amount` | `debit_amount - credit_amount` |
| `account_id` | line `account_id`, prefixed |
| `vendor_id` | NULL (journal lines never have a vendor) |
| `description` | entry `memo` (e.g. "Weekly wages earned by Viktor Duvall."); line descriptions are generic ("Wages earned.") |
| `txn_type` | entry `purpose` verbatim |

Resulting `txn_type` counts: cost_of_goods 75,863, bill 39,563, wage_accrual 26,705, interest_accrual 7,196, depreciation 5,923, wip_capitalization 3,399, wip_release 1,090, bad_debt 41, client_default 7, inventory_disposal 3.

Bill rows by account (canonical type): freight 14,287, wip 10,619 (asset), operating-expense 3,826, utilities 3,702, inventory 3,324 (asset), rent 1,672, insurance 814, accrued-interest 253 (liability), permits 236, income-tax 232, professional-fees 191, interest-expense 179, repairs 113, marketing 92, equipment 23 (asset).
So 13,966 bill rows land on asset accounts and 253 on a liability account; for those accounts `SUM(amount)` means "amount billed to the account", not the account balance.

Checks on this mapping (all pass):

- `account_id`, `vendor_id` and `txn_id` are unique across all companies; every transaction's account and vendor exist and belong to the same company.
- **P&L reconciliation:** for all 100 companies, `SUM(amount)` over canonical rows on expense accounts equals the sum of the non-Income lines of `reports/profit_and_loss.yaml` `full_period`, to the cent.
  P1-02 should keep this as a loader test.
- Largest absolute amount is 1,364,875.92, well inside `NUMERIC(14,2)`.

## Amount sign convention

Raw facts:

- Document amounts are unsigned: no negative values in any money field of bills, bill payments, invoices, sales receipts, customer payments, journal lines (debit and credit columns) or payroll runs.
- Direction comes from the document type (bill = money owed to a vendor) or from the debit/credit column of a journal line.
- There are no refunds, credit memos or vendor credits (DATA_README says so, and the `deposit_refund` purpose listed in SCHEMA.md never occurs).
- The bank feed is signed the opposite way to the canonical ledger: positive = money into checking (`direction: debit`, 172,661 rows), negative = money out (`direction: credit`, 61,949 rows), never zero, and `running_balance_amount` is consistent on every row.
  The loader does not use it; anyone who does must negate it.

Rule for the loader (exact):

```
bill line:                                  amount = Decimal(line.amount)                 # always > 0
journal line on an account whose
  classification == "expense":              amount = Decimal(debit_amount) - Decimal(credit_amount)
anything else:                              not loaded into ledger.transactions
```

A debit to an expense account is money spent (positive); a credit to an expense account reduces it (negative).
Result: 156,160 positive rows, 3,630 negative rows, 0 zero rows.
All negative rows are credits to `acct-manager-wages`: 3,399 `wip_capitalization` and 231 `wip_release`, where project-manager time is moved out of overhead into WIP or COGS.
They are reclassifications rather than refunds, but keeping them negative is what makes account totals match the P&L.

Examples (raw, then canonical):

| raw | canonical `txn_id` | `txn_date` | `amount` | `account_id` | `vendor_id` | `txn_type` |
|---|---|---|---:|---|---|---|
| company-002 bill `bill-operating-00004`, landlord, account line `acct-operating-expense` 500.00 | `company-002:bill-operating-00004:1` | 2025-10-13 | 500.00 | `company-002:acct-operating-expense` | `company-002:vend-landlord` | bill |
| company-002 product bill `bill-00008`, item line 600 x 6.15 = 3690.00 | `company-002:bill-00008:1` | 2025-10-10 | 3690.00 | `company-002:acct-inventory` | `company-002:vend-supplier` | bill |
| company-002 JE `je-cogs-00247`: Dr `acct-cogs` 166.05 / Cr `acct-inventory` 166.05 | `company-002:je-cogs-00247:1` (line 2 not loaded) | 2025-10-13 | 166.05 | `company-002:acct-cogs` | NULL | cost_of_goods |
| company-023 JE `je-payroll-00002`: Dr `acct-manager-wages` 1500.00 / Cr `acct-wages-payable` | `company-023:je-payroll-00002:1` | 2025-10-13 | 1500.00 | `company-023:acct-manager-wages` | NULL | wage_accrual |
| company-027 JE `je-labor-00244`: Dr `acct-wip` 262.50 / Cr `acct-manager-wages` 262.50 | `company-027:je-labor-00244:2` (line 1 not loaded) | 2025-11-11 | -262.50 | `company-027:acct-manager-wages` | NULL | wip_capitalization |
| company-027 JE `je-labor-00566`: Dr `acct-cogs` 262.50 / Cr `acct-manager-wages` 262.50 | `:1` and `:2` | 2025-12-12 | 262.50 and -262.50 | `acct-cogs` and `acct-manager-wages` | NULL | wip_release |
| company-002 bill payment `bpay-00003` (500.00 to landlord, settles `bill-operating-00004`); bank line `bank-000004` -500.00 credit | not loaded | | | | | |
| company-002 bank line `bank-000005` +116.96 debit (customer payment `rcpt-00004`) | not loaded | | | | | |

## IDs

- Raw IDs are company-scoped, as DATA_README says and the data confirms: 39,563 bills use only 17,116 distinct raw ids, 151,730 journal entries use 71,361, 3,700 accounts use 38, and 821 vendors use 30.
- Within one company every raw id is unique (checked for accounts, vendors, bills, bill payments, journal entries and bank lines).
- Account ids are stable across companies (`acct-rent` is "Rent and Lease" everywhere), so cross-company comparisons by raw account id work.
- `index.json` `company_id` and `dataset_id` (`world-<16 hex>`) are both unique per company; `company.yaml` `id` is not (always `company-main`).
- `doc_number` is advisory per DATA_README and must not be used as a key.

Recommended canonical IDs (deterministic, stable across reloads, traceable to the raw files, verified unique):

| canonical | format | example |
|---|---|---|
| `company_id` | raw `index.json` `company_id` | `company-017` |
| `account_id` | `{company_id}:{raw account id}` | `company-017:acct-freight` |
| `vendor_id` | `{company_id}:{raw vendor id}` | `company-017:vend-freight-vendor` |
| `txn_id` | `{company_id}:{raw bill or JE id}:{line_num}` | `company-017:bill-freight-00061:1` |

The `line_num` suffix is required because 231 `wip_release` entries produce two canonical rows each.
Planted rows from P1-03 need their own ids that cannot collide with raw ids (for example `{company_id}:planted-dup-0001:1`).

## Rows that don't fit

Excluded from `ledger.transactions`, with the reason:

| raw rows | count | why excluded |
|---|---:|---|
| `invoices`, `sales_receipts` | 84,451 and 28,501 | Income, not money spent. Their totals equal `index.json` revenue for 100 of 100 companies, so revenue is available if the team wants it later. |
| `customer_payments` | 143,616 | Cash collected against receivables. |
| `bill_payments` | 36,032 | Cash settlement of bills already loaded. Loading them would double-count vendor spend and, because each payment repeats its bill's vendor and amount a few days later, would raise duplicate-rule matches from 11,025 to 34,517. |
| JE lines on non-expense accounts | 183,240 | Balance-sheet movements and transfers: the other side of every loaded journal line, plus entries with no expense line at all: wage_payment 25,733, card_settlement 4,576, wip_capitalization from inventory 678, financing_draw 273, deposit_receipt 190, owner_distribution 152, opening_equity 100, financing_repayment 32. |
| `bank_transactions` | 234,610 | Derived feed that mirrors the documents above exactly. |
| `payroll_runs` | 25,733 | Derived; each mirrors one wage_payment entry. |

Multi-line and split cases:

- Bills: none; every bill is single-line, so there are no split bills.
- Journal entries: the two 5- and 6-line `client_default` entries in company-044 each have one expense line (a debit to `acct-operating-expense`), so each yields one canonical row.
- 231 `wip_release` entries have two expense lines (COGS debit, manager-wages credit) and yield two rows each.
- `wage_accrual` contains 2 "Hiring costs" lines on `acct-operating-expense` and 9 severance lines; they load like any other wage accrual.

## Template feasibility

Checked against the proposed canonical ledger (before planted anomalies).

| template_id | verdict | reason |
|---|---|---|
| `agg.total_by_account_period` | OK | Every company has 4 to 15 accounts with transactions (4 to 12 expense accounts). For asset and liability accounts the sum is "amount billed to the account", so the sampler should prefer expense accounts or the wording should say so. |
| `agg.total_by_vendor_period` | OK | 3 to 17 vendors per company, all with bills; 725 of 821 vendors have 5 or more bills. |
| `agg.count_by_account_period` | OK | Same accounts as above. |
| `agg.top_vendor_by_spend` | OK | Only bill rows have vendors, so "spend" means billed amount. |
| `agg.mom_change_account` | OK | 13 calendar months, 2025-10 to 2026-10; the first (from 10-06) and last (to 10-06) are partial, and 2025-10 has no previous month, so the sampler must not pick it. |
| `agg.share_of_expense` | OK | The denominator includes COGS, wages, depreciation and interest from journal lines and reconciles to the P&L. |
| `anom.duplicate_payments` | needs change | 39 companies (14 eval) have natural matches from recurring identical freight and subcontract (WIP) bills: 11,025 matching later rows in total, up to 1,535 in company-017 over the year, median 16 per company-month where any exist (max 204). The over-50 rejection then forces very short windows, and planted duplicates are mixed with legitimate recurring charges. The other 61 companies have no natural matches. Decide in P1-06: accept the mechanical definition and word questions as "same vendor and amount within 3 days", or narrow the definition. Planted copies (P1-03) must copy bill rows, the only rows with a vendor. |
| `anom.round_amounts_over` | needs change | 10,129 matching rows overall (wage_accrual 6,032, bill 4,037, cost_of_goods 56, wip_release 4), but 12 companies have none, including eval company-074 and company-077. The sampler must skip companies with no matches, or P1-03 must plant some. |
| `anom.vendor_outliers` | needs change | Only 475 rows in 73 of 821 vendors exceed 3 x the vendor median; 49 companies have any (17 of 30 eval, 32 of 70 train). The sampler must pick vendors that have at least one outlier, or P1-03 must plant outliers. A 2 x multiplier would cover 62 companies, 2.5 x would cover 53. |
| `book.accrual` | OK | Every company has expense accounts and 6 or 7 liability accounts (Accounts Payable, Wages Payable, Accrued Interest, Credit Card, Line of Credit, Customer Deposits, and Income Tax Payable except in the 4 nonprofits). |
| `book.prepaid_amortization` | needs change | No company has a prepaid account (0 accounts with "prepaid" in the name or subtype). Proposal: the loader adds one asset account "Prepaid Expenses" (`{company_id}:acct-prepaid-expenses`) to every company and it is disclosed in `DATASET_CHANGES.md`; otherwise drop the template. |
| `book.reclassify` | OK | Any bill row and a different expense account of the same company. |

## Missing data and proposed schema changes

The canonical DDL in Section 8.7 fits this data without any required change.
Points for the team to confirm before IC-1:

1. **Transactions scope.** Confirm that `ledger.transactions` holds bills plus expense journal lines only (accrual basis, reconciles to the P&L), with no income, payments or transfers.
2. **Revenue is out of scope.** Invoices, sales receipts, customers, receivables, payables, projects, estimates, items and the bank feed are not in the canonical schema.
   If we want revenue or AR questions, that needs an additive table (for example `ledger.income`) agreed before IC-1.
   Not recommended for now, since no template needs it.
3. **No prepaid account** (see `book.prepaid_amortization`).
   Proposed fix: add one account per company, disclosed.
   This is a data change, not a schema change.
4. **Most rows have no vendor.** 120,227 of 159,790 canonical rows (75%) have `vendor_id` NULL, because journal lines never carry a vendor; vendor templates rely on the 39,563 bill rows.
5. **No refunds or credits exist.** The only negative amounts are the 3,630 manager-wage reclassifications.
   If we want refund cases, they must be injected and disclosed.
6. **The books are clean.** Books balance and the bank feed matches them exactly (DATA_README), so anomaly tasks depend on planted anomalies plus the natural patterns described above.
7. **Uneven size.** Canonical rows per company range from 92 to 11,601 (median 704); 75,863 rows (47%) are per-delivery `cost_of_goods` entries from wholesale and mixed companies.
8. **Vendor names do not match raw vendor ids.** Display names are random relative to the role in the raw id (company-002 `vend-electricity` is "Copperfield Water District"; company-001 `vend-logistics-vendor` is "Whitlock & Daughters Nursery").
   The raw ids, which encode the role, become part of the canonical `vendor_id` and so are visible to the agent.
9. **Undocumented and unused enum values.** `card_settlement` appears 4,576 times but is not in the SCHEMA.md purpose list; `deposit_refund` is in the list but never occurs.
10. **F7 in Section 4** should be updated to VERIFIED with a pointer to this file (not done here; this change only adds `docs/DATASET.md`).

## Split (Section 9.1)

Preview of what P1-02's loader will apply: sort the 100 canonical `company_id`s ascending, shuffle with `random.Random(7)` (Python 3.12), and take the first `max(2, round(0.30 * 100)) = 30` as eval; the remaining 70 are train.

Eval in shuffled order: company-034, company-026, company-100, company-085, company-079, company-082, company-022, company-094, company-083, company-002, company-023, company-001, company-062, company-074, company-025, company-050, company-027, company-045, company-077, company-080, company-048, company-003, company-052, company-039, company-015, company-049, company-011, company-033, company-017, company-095.

**Eval (30, sorted):** company-001, company-002, company-003, company-011, company-015, company-017, company-022, company-023, company-025, company-026, company-027, company-033, company-034, company-039, company-045, company-048, company-049, company-050, company-052, company-062, company-074, company-077, company-079, company-080, company-082, company-083, company-085, company-094, company-095, company-100.

**Train (70, sorted):** company-004, company-005, company-006, company-007, company-008, company-009, company-010, company-012, company-013, company-014, company-016, company-018, company-019, company-020, company-021, company-024, company-028, company-029, company-030, company-031, company-032, company-035, company-036, company-037, company-038, company-040, company-041, company-042, company-043, company-044, company-046, company-047, company-051, company-053, company-054, company-055, company-056, company-057, company-058, company-059, company-060, company-061, company-063, company-064, company-065, company-066, company-067, company-068, company-069, company-070, company-071, company-072, company-073, company-075, company-076, company-078, company-081, company-084, company-086, company-087, company-088, company-089, company-090, company-091, company-092, company-093, company-096, company-097, company-098, company-099.

Composition:

| split | wholesale | construction | mixed | pos | appointments | recurring | nonprofit | small / medium / large | distressed |
|---|---:|---:|---:|---:|---:|---:|---:|---|---:|
| eval | 7 | 7 | 5 | 7 | 2 | 1 | 1 | 10 / 13 / 7 | 2 |
| train | 15 | 15 | 7 | 7 | 8 | 15 | 3 | 27 / 19 / 24 | 5 |

Eval has only 1 of the 16 recurring-services companies; the spec forbids re-running with another seed, so this is just noted.

## Loaded ledger (P1-02, P1-03)

Loaded by `env/scar_env/load_ledger.py` (`uv run python -m scar_env.load_ledger --data-dir <hackathon-100 dir>`), which truncates and reloads all ledger tables in one transaction.
Every modification of the raw data is listed in `docs/DATASET_CHANGES.md`.

| table | rows |
|---|---:|
| `ledger.companies` | 100 (30 eval, 70 train, as in "Split") |
| `ledger.accounts` | 3,800 (3,700 raw plus one added "Prepaid Expenses" per company) |
| `ledger.vendors` | 821 |
| `ledger.transactions` | 160,779 (159,790 mapped from the raw files plus 989 planted) |
| `ledger.planted_anomalies` | 989 (5 to 15 per company, median 10; 328 in eval companies, 661 in train) |

- Excluding planted rows, every per-company figure in the Companies table (name, industry, txn rows, bills, vendors, first and last txn date, split) matches the loaded ledger exactly.
- Planted copies are dated 1 to 3 days after their original (+1: 333, +2: 317, +3: 339) and never extend a company's date range.
- They sit on whatever account the original bill used: 626 on expense accounts, 355 on asset accounts (inventory, WIP, equipment) and 8 on Accrued Interest.
- The Section 9.5 duplicate-payment rule over the loaded ledger flags 12,063 later rows: the 11,025 natural matches, the 989 planted copies, and 49 natural bills that match only because a planted copy precedes them by 3 days or less.
- The P&L reconciliation (planted rows excluded), the split, the row counts and the foreign keys are asserted by `test_real_dataset` in `env/tests/test_load_ledger.py` (run with `SCAR_REAL_DATA_TESTS=1`).
- A load takes about 35 seconds, almost all of it parsing 147 MB of YAML with libyaml; two consecutive loads give identical table checksums:

| table | md5 checksum (rows as text, primary-key order) |
|---|---|
| `companies` | `9e48a212d74453b3c39ed4894169d750` |
| `accounts` | `6ab8ff0ca0a6699d0952f51cd4b998ab` |
| `vendors` | `d47f535bf823c878151f2a1e2c11dc54` |
| `transactions` | `7c7dcfe55f41f402e283597fa58540f1` |
| `planted_anomalies` | `b96abbd445f98ca4eed187e61f5496dc` |

## Difficulty tuning (P1-07)

Run on 2026-10-18 against the registered `base` model (`Qwen/Qwen3-4B-Instruct-2507` on a Veri L4 deployment), through the agent service, on train pool positions 0-199.
The prompt was `system_v1` with step 6 reworded to describe `submit_answer` as a tool call (PR #2); with the original wording the model wrote the call as plain text and passed 0 of 30 seed tasks.
Overall base failure: 132 of 200 (66%), inside the 40-70% target, so the parameter ranges were not changed.

| template | passed / runs |
|---|---|
| `agg.count_by_account_period` | 2 / 15 |
| `agg.mom_change_account` | 1 / 4 |
| `agg.share_of_expense` | 1 / 4 |
| `agg.top_vendor_by_spend` | 5 / 8 |
| `agg.total_by_account_period` | 6 / 18 |
| `agg.total_by_vendor_period` | 8 / 18 |
| `anom.duplicate_payments` | 4 / 22 |
| `anom.round_amounts_over` | 5 / 23 |
| `anom.vendor_outliers` | 10 / 22 |
| `book.accrual` | 22 / 22 |
| `book.prepaid_amortization` | 3 / 22 |
| `book.reclassify` | 1 / 22 |

By family: aggregation 23 / 67, anomaly 19 / 67, bookkeeping 26 / 66.
By difficulty: 1: 33 / 68, 2: 15 / 66, 3: 20 / 66.
Stop reasons: submitted 94, model_error 52, no_submit 47, timeout 5, max_steps 2.
The 52 `model_error` runs were the agent's 60 s per-turn timeout at 32 concurrent runs on one L4, not model failures; later runs use lower concurrency.

## Train pool and eval set (P1-08)

- Train pool: `POST /tasks/generate` with split train, n 2000, seed 42 (TRAIN_POOL_SEED), origin generated; 2,000 positions over all 70 train companies, 666 or 667 tasks per family, difficulty spread 222-223 per family and level.
- `eval_v1`: split eval, n 300, seed 1234 (EVAL_SET_SEED); exactly 100 tasks per family (difficulty 34 / 33 / 33), all 30 eval companies, all eval-split.
- `eval_v1` sha256: `59de77355e6bf9ae83fd6ac1387d8ad240c7605694f5057073eb2239cf0b2888`.
- Generated once; never regenerated without a team decision.
