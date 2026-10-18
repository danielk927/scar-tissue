# Person 2 overnight handoff (2026-10-18, written about 01:30 PDT)

From Person 1, who is also covering Person 3's work package.
Person 2's Claude: work through this list top to bottom while Person 2 sleeps.
Stay inside Person 2's paths (`agent/`, `web/`, `fixtures/runs_sample.jsonl`, `fixtures/failures_sample.jsonl`, `docs/JEV.md`, `docs/SENTRY.md`, `docs/DEMO.md` except the Person 3 heading).
Commit on `p2/<topic>` branches and push often, with clear messages; merge your own-path PRs after tests pass (SPEC 7.3).
Anything that touches `schemas/` or `docs/SPEC.md` needs a PR for Person 1.
Deadline: Devpost submission Oct 18, 12:00pm PDT; demo freeze starts around 08:00 PDT.

## Morning update (05:00 PDT)

- Generation 1 results (held-out `eval_v1`, base 0.45): D-s0-g1 (Veri LoRA on 225 examples) scored **0.71** and improved every family (aggregation 0.29 to 0.79, anomaly 0.49 to 0.55, bookkeeping 0.61 to 0.79). B-s0-g1 (lessons in the prompt) scored 0.083: its lessons spell out `submit_answer({"number": X})` as text and the model copies that instead of calling the tool. C-s0-g1 (GEPA) is running.
- Gate rule changed (schemas 1.3.0, SPEC 9.9): the parent is re-run on the same regression set, and the candidate must score at least the parent's regression score minus 0.05. `base` re-passed only 81 of its own 90 regression tasks, so the old 95% bar rejected everything. D-s0-g1 is being re-gated under the new rule.
- Web: `GenerationRecord` has a new optional `parent_regression_score` (null on older records). Consider showing "regression 0.867 vs parent 0.900" in the dashboard table next to `regression_score`, and use 0.71 vs 0.45 as the headline in the demo script once D-s0-g1's re-gate finishes.
- Train now runs one agent batch at a time across lineages (two overlapping gates timed out half their model calls), and picked up your timeout-retry fix.

## 0. Current state (read first)

- `main` has: env service (all of P1-01..P1-14), train service with the loop runner, data builder, gate, Veri backend, base registration, smoke scripts (P3-02..P3-10), and merged PRs #1 (Veri spec change, D-03/D-04), #2 (`system_v1` step 6 reworded) and #3 (constants 1.1.0: `K_SAMPLES` 4, `VARIANTS_PER_FAILURE` 5, `FAILURE_CAP_PER_GEN` 25).
- **Base model is live**: `Qwen/Qwen3-4B-Instruct-2507` on one Veri L4 deployment `5a6d7ab804e0`, registered as `base` (endpoint `https://api.veri.studio/v1/deployments/5a6d7ab804e0`, model id `scar-base`). It scales to zero after 15 idle minutes and takes about 15 minutes to wake: if your first call gets 503s or timeouts, wait, or run `cd train && uv run python -m scar_train.veri wake`.
- **Measured on base**: with the old `system_v1` wording 0/30 seed tasks passed (26 runs wrote `submit_answer(number=...)` as plain text); with PR #2's wording 12/30 seed tasks and 68/200 train-pool tasks pass (66% failure, inside P1-07's band). Throughput about 21 runs/min at 16 concurrent runs; about 5% of runs hit the 60 s per-turn timeout (more at 32 concurrent).
- **The experiment database lives on Person 1's laptop** (train pool, `eval_v1`, generations). Generation 0 (base eval on `eval_v1`) is running now; generation 1 of D-s0, then B-s0 and C-s0, follow overnight. You cannot reach that DB from your laptop; build and test against your own local stack (section 1) and against the shared Veri deployment.
- **Shared GPU and money**: every agent run on any laptop goes through the same single L4 replica, and the Veri balance is about $20. Keep your own runs small (at most ~30 runs per batch, `max_concurrency` <= 8), never create Veri deployments or training jobs, and never call `/loop/run`.
- **Keys**: the new repo-root `.env` (sent to Person 2 by Person 1) has `TYPESAFE_API_KEY` (verified: `jev-1.14.0` answered a triage call in 0.1 s), `MODEL_API_KEY`, `VERI_API_KEY`, Groq `REFLECTION_LM_*`, Sentry DSNs. The Gemini key (`PARSE_LM_*`, `DEV_MODEL_*`) is the free tier: about 20 requests/day per model, already rate-limited.

## 1. Sync and run a local stack (do first, about 20 min)

1. `git pull origin main`; install the new `.env` at the repo root.
2. Database: `docker compose up -d db && scripts/init_db.sh` (or any Postgres 16+ whose user can create roles; `scripts/init_db.sh --reset` recreates).
3. Ledger and tasks: `cd env && uv sync && uv run python -m scar_env.load_ledger --data-dir <extracted hackathon-100 dir> && uv run python -m scar_env.seed_tasks load`.
4. Register base: `cd train && uv sync && uv run python -m scar_train.register_base --endpoint-base-url https://api.veri.studio/v1/deployments/5a6d7ab804e0 --endpoint-model-id scar-base`.
5. Run the three services: `cd env && uv run uvicorn scar_env.main:app --port 8001`, `cd agent && uv run uvicorn scar_agent.api:app --port 8002`, `cd train && uv run uvicorn scar_train.main:app --port 8003`.
6. Check: `scripts/smoke_ic1.sh` (30 seed tasks on base; expect roughly 10-14 passes). Record the result in `docs/DEMO.md` or `docs/SENTRY.md`.
7. If you want a local train pool for your own testing: `curl -X POST localhost:8001/tasks/generate -H 'Content-Type: application/json' -d '{"split":"train","n":2000,"seed":42,"origin":"generated"}'` (deterministic params; task IDs differ from Person 1's DB).

## 2. Review the merged PRs (15 min)

Comment on https://github.com/danielk927/scar-tissue/pull/1, /pull/2 and /pull/3 if anything in your area needs changing (they were merged by the admin to unblock the runs).
PR #2 changed step 6 of `agent/scar_agent/prompts/system_v1.md` and one assertion in `agent/tests/test_runner.py`; it must stay frozen after IC-1 because every training example contains it.

## 3. Agent fixes found in Person 1's end-to-end runs (highest value; each with tests)

1. **`/failures/mine` re-triage (SPEC 9.4).** `mining.mine` returns existing records whenever any exist for (arm, seed, generation), so failed runs added later (judge runs, or traffic after a resumed collect) are never triaged. SPEC 9.4 says to triage failed runs that have no `app.failures` row yet, then return all records for the generation; idempotency means "no new Jev calls for runs already triaged". Fix it so the cap and order (9.4) are applied over all records of the generation, keep the existing tests green, add tests for "new failed runs after a first mine".
2. **Invalid tool-call JSON poisons the next request.** docs/VERI.md (search "invalid tool-call JSON") found that vLLM fails the next request if the model emitted malformed tool-call arguments earlier in the transcript; those runs end as `model_error` instead of letting the model recover. Reproduce against the live base (a transcript with a `{"__raw__": ...}` tool call), decide the fix consistent with SPEC 9.2 and R7 (training data must match inference), and document it in `docs/DECISIONS.md` under Person 2.
3. **Per-turn timeouts.** About 5% of runs at 16 concurrent end with `model_error` from the 60 s per-turn timeout (the replica queues requests). Check whether the timeout wraps the openai client's own retries (`max_retries=2` inside `asyncio.timeout(60)`), whether queue time should count, and whether a retry on timeout (once) is safe. Measure before and after on 30 seed tasks at `max_concurrency` 8 and 16; record numbers in `docs/DECISIONS.md`.
4. **`run_batch` retries can duplicate runs.** When one run in a batch fails, the others still store results, so a caller's retry creates duplicate traffic runs. Env's `/simulate` and train's data builder already reuse stored runs, but consider an idempotency key (for example a client-supplied request id) or document the behavior in the API docstring.
5. **Text-written submits.** With the new wording, 9 of 30 seed runs still wrote `submit_answer {"number": ...}` as text and ended `no_submit`. This is learnable (the loop should fix it), so do not paper over it in the loop; but double-check the agent's no-tool nudge text matches SPEC 9.2 exactly.

## 4. Jev (P2-06 follow-up, P2-07)

1. Make one real `/failures/mine` call on your local stack: run 20 traffic-purpose runs on base (`/agent/run_batch`, purpose `traffic`, arm D, seed 7, generation 1, `max_concurrency` 8), then `POST /failures/mine` for (D, 7, 1). Check inclusion rule, cap and ordering on real records.
2. Record in `docs/JEV.md`: the console's rate limits for our org (https://console.typesafe.ai), latency per triage, and the model the API reports.
3. **Spot check (P2-07) needs Person 2 in the morning**: prepare `docs/JEV.md` section with 20 randomly chosen (seeded) triaged failures: for each, the question, a short steps summary, final answer, verifier detail, and Jev's cause and learnable_p, with an empty "human label" column. Person 2 labels them after waking; then compute the agreement rate.
4. Replace the hand-written failures fixture: `uv run python -m scar_agent.fixtures export-runs` and `export-failures` from your local runs (P2-04: at least 20 runs with at least 8 failures, at least 5 failure records), then `validate`, and commit `fixtures/runs_sample.jsonl` and `fixtures/failures_sample.jsonl`.

## 5. Judge flow (P2-08)

1. The Gemini key allows about 20 requests/day, which is not enough for rehearsals and judging. Switch `PARSE_LM_*` to Groq in your code's expectations (OpenAI-compatible: base URL `https://api.groq.com/openai/v1`, the same key as `REFLECTION_LM_API_KEY`, model `openai/gpt-oss-120b` or another Groq model with reliable JSON output). Verify JSON-mode or schema support on Groq's docs before relying on it (R4). Tell Person 1 the exact `.env` values to set; do not edit `.env` in git.
2. Acceptance from SPEC P2-08 on the local stack: five sample questions parse to the right template with valid params; one unparseable question returns a friendly `valid=false` with three example questions. Use questions from the table in section 8.
3. `/judge/run`: creates the task via env `/tasks/from_params` and runs it on train's `/models/current?arm=D&seed=0` model with `generation` = latest finished D-s0 generation + 1. Test that the expected answer is never sent to the browser before the run finishes.

## 6. Web app (P2-09)

1. All three pages against live local services: `/ask` (judge UI), `/runs/[run_id]` (transcript, verifier detail, Jev cause, Sentry link, "Re-run on newest model"), `/dashboard` (eval score vs generation per arm, mean with min-max band over seeds, plus a generations table).
2. Dashboard rules: the score after a generation is the candidate's eval score if promoted, otherwise the parent's (same as `experiments/scar_experiments/report.py`); generation 0 is the shared base score; **hide records whose `notes` start with `[tiny]` or mention "fake trainer"** (smoke runs, never reported).
3. Make every service URL configurable at runtime (env vars), because the demo runs the web app against Person 1's laptop over the venue network (`http://<Person 1 LAN IP>:8001/8002/8003`).
4. Pixel-check every page at laptop and projector sizes (1280x800 and 1920x1080): loading and error states, long transcripts, empty dashboard (no generations yet), a generation that was rejected.
5. Session Replay and browser tracing with propagation to the agent service; confirm a judge click connects to the backend run trace in Sentry.

## 7. Sentry (P2-03 follow-up, P2-10)

1. Env and train services already init Sentry with Logs (`scar_env/main.py`, `scar_train/main.py`, copied from your docs/SENTRY.md section 6 snippet). In Sentry, confirm logs from `service:env` and `service:train` arrive and are filterable by `run_id`; note anything missing for Person 1.
2. Saved views for the demo: issues list (grouped `agent_run_failed`), one representative failed traffic trace, the AI agents dashboard. Put the links in `docs/DEMO.md`.
3. `scripts/smoke_ic1.sh` uses purpose `smoke`, which never creates Sentry issues, so IC-1's "failed runs grouped into issues" needs a traffic or judge run: run 10 traffic runs on base locally and check the issues group per (template, stop_reason).
4. Screenshots for the submission (SPEC 17): issues grouped by failure mode, one agent trace, one session replay. Save them under `docs/screenshots/` (create it) and list them in `docs/DEMO.md`.

## 8. Demo (P2-11)

1. Fill `docs/DEMO.md` "Prepared questions" with 10 judge questions on train companies that base fails. Candidates below are real base failures from Person 1's database (train-pool tasks, PR #2 prompt, no infrastructure errors); rephrase them as a judge would type them and confirm on your local stack that `/judge/parse` maps each to the right template.
2. Write the demo script per SPEC 14 with exact clicks and fallbacks; time it (about 4 minutes).
3. Rehearsal notes section ready for the team in the morning.

### Base-failure candidates (from Person 1's DB, 2026-10-18)

| # | Company (company_id) | Question | Template | How base failed |
|---|---|---|---|---|
| 1 | Riverstage Arts Alliance (`company-099`) | Count the Operating Expense transactions dated between 2025-10-31 and 2025-12-07 (both dates included). | `agg.count_by_account_period` | never submitted (wrote the answer as text) |
| 2 | Clearbook CPA Group (`company-061`) | How many entries hit Income Tax Expense with dates from 2026-01-01 to 2026-01-31, inclusive? | `agg.count_by_account_period` | never submitted (wrote the answer as text) |
| 3 | Driveline Machinery Supply (`company-010`) | What is the number of transactions posted to the Legal and Professional Fees account from 2025-11-15 through 2026-09-12? | `agg.count_by_account_period` | never submitted (wrote the answer as text) |
| 4 | Brightwater Community Fund (`company-097`) | How much more was recorded in Insurance in May 2026 than in April 2026? Answer with a negative number if it went down. | `agg.mom_change_account` | never submitted (wrote the answer as text) |
| 5 | Brightface Sign Co (`company-053`) | What is the month-over-month change in Cost of Goods Sold for June 2026, computed as the June 2026 total minus the May 2026 total? | `agg.mom_change_account` | never submitted (wrote the answer as text) |
| 6 | Northgate Portfolio Partners (`company-071`) | By how much did Wage Expense change from February 2026 to March 2026? Give March's total minus February's total, negative if it went down. | `agg.mom_change_account` | never submitted (wrote the answer as text) |
| 7 | Riverstage Arts Alliance (`company-099`) | What percentage of our total expenses was Rent and Lease from 2025-12-01 through 2026-10-03, inclusive? Use every expense account for the total and round to two decimal places. | `agg.share_of_expense` | never submitted (wrote the answer as text) |
| 8 | Juniper & Rye Coffeehouse (`company-076`) | Between 2025-10-24 and 2026-10-05, both included, what share of all expense-account spending went to Wage Expense? Answer as a percentage rounded to 2 decimals. | `agg.share_of_expense` | never submitted (wrote the answer as text) |
| 9 | Brightface Sign Co (`company-053`) | Which vendor did we spend the most with between March 3 and April 22, 2026, inclusive? If several vendors tie for the top spot, list all of them. | `agg.top_vendor_by_spend` | submitted a wrong answer |
| 10 | Hearthworn Remodeling (`company-030`) | Find the vendor with the highest total billed amount from 2025-11-30 through 2026-10-05; list all vendors if there is a tie. | `agg.top_vendor_by_spend` | never submitted (wrote the answer as text) |
| 11 | Brightline Electric & Supply (`company-046`) | Identify the vendor or vendors with the greatest total spend in transactions dated 2025-10-09 to 2026-08-09, inclusive. | `agg.top_vendor_by_spend` | submitted a wrong answer |
| 12 | Oddjob Specialty Trades & Supply (`company-051`) | Add up every Taxes and Licenses transaction dated 2026-07-25 through 2026-10-06. What is the total? | `agg.total_by_account_period` | never submitted (wrote the answer as text) |
| 13 | Courtyard Commons Apartments Construction (`company-040`) | From 2025-10-08 to 2026-09-15 inclusive, what did Taxes and Licenses transactions sum to? | `agg.total_by_account_period` | never submitted (wrote the answer as text) |
| 14 | Birchfield Custom Homes (`company-029`) | What is the total of all Repairs and Maintenance transactions from 2026-08-18 through 2026-10-03, inclusive? | `agg.total_by_account_period` | never submitted (wrote the answer as text) |
| 15 | Brightline Electric & Supply (`company-046`) | Add up every transaction with Lowry Electrical Contractors dated from 2025-11-12 to 2026-06-07, both dates included. What is the total? | `agg.total_by_vendor_period` | submitted a wrong answer |
| 16 | Birchfield Custom Homes (`company-029`) | From 2026-07-07 to 2026-07-20 inclusive, what was our total spend with the vendor Copper Sitework Services? | `agg.total_by_vendor_period` | submitted a wrong answer |
| 17 | Hearthworn Remodeling (`company-030`) | What did we spend with Rasmussen Insurance Agency from May 1 to May 28, 2026, inclusive? | `agg.total_by_vendor_period` | never submitted (wrote the answer as text) |
| 18 | Oakmoor Property Management (`company-065`) | Check 2025-12-27 to 2026-03-25 for duplicate payments. Return the later transaction of every same-vendor, same-amount pair that is 0 to 3 days apart with both transactions in the period (ties on date go by transaction ID). | `anom.duplicate_payments` | submitted a wrong answer |
| 19 | Birchfield Custom Homes (`company-029`) | Find possible duplicate payments from 2026-01-19 to 2026-02-16: for every pair of transactions with the same vendor and the same amount dated at most 3 days apart (both inside the period), list the later one. For same-day pairs, the later one is the one with the larger transaction ID. | `anom.duplicate_payments` | submitted a wrong answer |
| 20 | Oakmoor Property Management (`company-065`) | Find possible duplicate payments from 2026-05-19 to 2026-08-13: for every pair of transactions with the same vendor and the same amount dated at most 3 days apart (both inside the period), list the later one. For same-day pairs, the later one is the one with the larger transaction ID. | `anom.duplicate_payments` | submitted a wrong answer |
| 21 | Sawhorse Home Remodelers (`company-042`) | Find every transaction between 2026-03-28 and 2026-04-10 (both included) whose amount is a multiple of $100 and at least $100.00. | `anom.round_amounts_over` | submitted a wrong answer |
| 22 | Stitchform Apparel Co (`company-004`) | List the transaction IDs of every transaction dated 2026-06-24 through 2026-07-22 whose amount is an exact multiple of $100 and at least $100.00. | `anom.round_amounts_over` | submitted a wrong answer |
| 23 | Quietwater Counsel LLP (`company-091`) | Identify transactions dated 2025-12-01 to 2025-12-13, inclusive, with round amounts divisible by $100 that are greater than or equal to $200.00. | `anom.round_amounts_over` | submitted a wrong answer |
| 24 | Birchfield Custom Homes (`company-029`) | Which Okonkwo Grocers LLC transactions from 2025-10-16 to 2026-01-03, inclusive, were more than 3 times the vendor's median transaction amount? Use the median over all of Okonkwo Grocers LLC's transactions in the ledger. | `anom.vendor_outliers` | submitted a wrong answer |
| 25 | Conduitworks Electrical Supply (`company-008`) | From 2026-03-03 to 2026-04-01 inclusive, which charges from Vale Crossing Fulfillment exceeded 3 times that vendor's overall median transaction amount? | `anom.vendor_outliers` | submitted a wrong answer |
| 26 | Granary Commercial Builders (`company-043`) | Find Clearwater Works Co. transactions dated 2026-03-11 through 2026-04-28 whose amount is over 3x the median of all Clearwater Works Co. transactions (any date). | `anom.vendor_outliers` | never submitted (wrote the answer as text) |
| 27 | Copper Kettle Cafe (`company-073`) | Book a journal entry dated February 28, 2026 accruing $4,178.47 of unpaid Project Manager Wages to Wages Payable. | `book.accrual` | submitted a wrong answer |
| 28 | Signalcraft Systems Integration (`company-054`) | Set up 2 month-end journal entries, starting January 2026, that amortize $20,378.78 of Prepaid Expenses into Operating Expense. Split it evenly, rounded to cents, with any leftover cents in the final month. | `book.prepaid_amortization` | submitted a wrong answer |
| 29 | Lumenwork Video (`company-044`) | A $10,105.29 equipment rental prepayment sits in Prepaid Expenses. Amortize it into Equipment Rental over 6 months beginning October 2025: one journal entry dated the last day of each month, each amount rounded to cents, with the final month absorbing the rounding. | `book.prepaid_amortization` | never submitted (wrote the answer as text) |
| 30 | Signalcraft Systems Integration (`company-054`) | Transaction company-054:pbill-00392:1 belongs in Subcontractors. Post one journal entry on its date to reclassify the whole amount there. | `book.reclassify` | never submitted (wrote the answer as text) |
| 31 | Lanternwade Commercial Realty (`company-067`) | Transaction company-067:bill-software-00238:1 belongs in Insurance. Post one journal entry on its date to reclassify the whole amount there. | `book.reclassify` | submitted a wrong answer |
| 32 | Oddjob Specialty Trades & Supply (`company-051`) | Transaction company-051:bill-utilities-00151:1 belongs in Rent and Lease. Post one journal entry on its date to reclassify the whole amount there. | `book.reclassify` | submitted a wrong answer |

## 9. Report back

Before Person 2 wakes, push everything and append a short "Person 2 overnight status" section to the end of this file (on a `p2/` branch via PR, or tell Person 1): what is done, what failed, and what needs a human.
