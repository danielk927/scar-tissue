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
- 2026-10-17: Template prompts show names, ISO dates, "March 2026" and "$1,234.56", never raw IDs, except `book.reclassify`, which shows the txn_id (vendor + date + amount is not unique).
- 2026-10-17: Template definitions pinned in `docs/TEMPLATES.md`: duplicate pairs need both transactions in the period and same-day pairs break ties by the larger txn_id; vendor median = exact mean of the two middle values, outliers strictly above 3x; share rounds half away from zero; mom_change = month minus previous month.
- 2026-10-17: Difficulty: period aggregations use 7-31 / 32-92 / 93-366 day periods for difficulty 1 / 2 / 3; other templates support only their base difficulty. Generation cycles difficulty 1, 2, 3 within each family.
- 2026-10-17: Prepaid tasks amortize only into expenses a business prepays and use at most 6 months (fits MAX_STEPS); reclassify targets are operating accounts only.
- 2026-10-17: `/tasks/from_params` accepts periods overlapping the data range, skips the degenerate rule but rejects undefined answers; judge tasks get the template's base difficulty.
- 2026-10-17: Seed tasks: 30 hand-written prompts (`env/scar_env/seed_tasks.py`), answers from template SQL, task IDs fixed by `fixtures/tasks_seed.jsonl`; `load` re-verifies answers against the ledger.
- 2026-10-17: Learn-step B: lesson ID = `{lineage}:{failure_id}` (one lesson per failure); prompt keeps the newest lessons (by generation, then failure_id) under a chars/3 token estimate of 3,000.
- 2026-10-17: Learn-step C: train/val pool draw `random.Random(f"gepa:{seed}:{generation}")`, first 50 to train, next 50 to val; candidate IDs `C-cand-<sha256(text)[:16]>`; a custom gepa stopper keeps agent runs strictly within GEPA_MAX_METRIC_CALLS (may leave up to 56 unspent); a dead reflection LM or an all-infrastructure-failure batch is a 503, never a silently stored seed prompt.
- 2026-10-17: Report: the score after a generation is the candidate's if promoted, else the parent's (also for failed generations); arm A repeats generation 0. Lives at `experiments/scar_experiments/report.py`.
- 2026-10-17: Mutator seeds with `random.Random(f"{failure_id}:{seed}:{i}")` instead of SPEC 9.6's `hash((failure_id, seed, i))`, which is salted per process and breaks R8 (test runs /mutate under two PYTHONHASHSEED values).
- 2026-10-17: Mutator: variants 0-1 use the original company, the rest round-robin over other train companies in a seeded order; the last round-half-up(0.3 n) variants use difficulty + 1 when supported; n = 2 or 3 cannot reach three companies. Idempotent per failure (existing variants are returned whatever the seed or n), guarded by an advisory lock; only included failures on train tasks (409 otherwise); a paraphrase LM error is a 503.
- 2026-10-17: Simulator: reuses stored traffic runs matching (arm, seed, generation, task, model_version, prompt_version), skipping infrastructure-failed runs, and sends one batch for the rest at max_concurrency 16 with an agent timeout of (ceil(n/16)+1) x 180 s; an all-infrastructure-failure batch is a 503.

## Person 2

- 2026-10-17: Model errors: any `openai.APIError` (after the client's 2 retries), a turn over 60 s, or a malformed response (no choices, bad role) ends the run with `stop_reason="model_error"`.
- 2026-10-17: Any non-200 or connection failure from env `/tools/execute` counts as `tool_error` (9.2 names only 503 and timeout).
- 2026-10-17: If env `/verify` is unreachable, the run is not stored and the agent API returns 503.
- 2026-10-17: `system_v1` text = `agent/scar_agent/prompts/system_v1.md` with the trailing newline stripped. Person 1: use the same when composing arm B prompts.
- 2026-10-17: Jev triage state leaves `submit_answer` out of `steps` (it is in `final_answer`) and keeps the first 12 steps. A cause label Jev returns that is not a `FailureCause` maps to `other`.
- 2026-10-17: `exclusion_reason` codes: `learnable_p_below_threshold`, `cause_<cause>`, `stop_reason_<stop_reason>`, `over_cap`.
- 2026-10-17: Mining is all or nothing: every Jev call first, then one transaction. If Jev fails, nothing is stored and the API returns 503. Idempotency = "any record exists for this arm/seed/generation".
- 2026-10-17: Jev client: 10 s per attempt, up to 3 retries inside a 30 s budget (our reading of 7.4; see `docs/JEV.md`).
- 2026-10-17: Judge generation = latest D-s0 generation with status `promoted` or `rejected`, plus one (1 if none).
- 2026-10-17: Unmatched judge questions get three example questions built from that company's first expense account and first vendor.
- 2026-10-17: "Re-run on newest model" calls `/agent/run` on the same `task_id` with the current D-s0 model, `purpose="judge"`, and the judge generation rule.
- 2026-10-17: Sentry: runs with `purpose="judge"` continue the caller's trace (judge click → run in one trace); every other run gets a fresh trace. Chat spans also carry `gen_ai.provider.name="openai"` and `gen_ai.response.model` (Sentry docs require them); tool I/O uses `gen_ai.tool.call.arguments` / `.result`; messages are converted to Sentry's `{role, parts}` shape. Details in `docs/SENTRY.md`.
- 2026-10-17: Dashboard plots the eval score of the version in use after each generation (`eval_score` if promoted, `parent_eval_score` if rejected). Arm A is drawn flat at the base score when it has no generation records.
- 2026-10-17: The web reaches services through Next.js rewrites `/api/{agent,train,env}/*` (same origin, so no CORS and trace headers pass through); proxy timeout 200 s.
- 2026-10-17: Session Replay rate comes from `NEXT_PUBLIC_SENTRY_REPLAY_RATE` (default 0, set 1 for the demo) because the free plan has 50 replays; error replays stay at 100%.
- 2026-10-17: Web pins `next@16.3.8` and `@sentry/nextjs@11.4.0` (newest releases at least two weeks old).
- 2026-10-18: The agent loop re-attaches provider-only tool-call fields (Gemini's `extra_content` thought signature, required on the next turn) to outgoing requests, keyed by tool-call ID within one run. Stored `RunResult.messages` never contain them, so the contract shape is unchanged; a no-op for providers without the field.
- 2026-10-18: Mining re-triage (supersedes the 2026-10-17 "any record exists" idempotency line): `/failures/mine` triages every failed traffic/judge run of the generation that has no record yet, then re-applies the cap and order over all records of the generation in one transaction (records excluded only by `over_cap` compete again, so a late judge failure can displace a traffic one). Runs already triaged never cost another Jev call; concurrent mines are serialized by row locks and `ON CONFLICT (run_id) DO NOTHING`.
- 2026-10-18: Per-turn timeouts: the 60 s `asyncio.timeout` wrapped the openai client's own retries (`max_retries=2`) and counted replica queue time (the request waits at the Veri gateway; we cannot separate queueing from generation). A timed-out model call is now retried once with a fresh 60 s; a second timeout is `model_error`, and the 180 s whole-run timeout still bounds the run. Safe because a chat completion is stateless and vLLM aborts the abandoned request on disconnect. Measured on the 30 seed tasks against the shared base replica (also serving Person 1's overnight runs, so noisy): before, infrastructure failures (model_error + timeout) were 4/30 at max_concurrency 8 and 2/30 at 16; after, 3/30 at 8 (1 model_error, 2 runs then hit the 180 s run timeout) and 1/30 at 16. 4 of 6 retried calls succeeded. Pass rates were unchanged (12-14/30).
- 2026-10-18: `/agent/run` and `/agent/run_batch` stay non-idempotent (an idempotency key would change `RunBatchRequest`, a shared contract): attempts are stored as they finish, so retrying a batch that failed with a 503 stores duplicates. Documented in the endpoint docstrings; callers reuse stored runs first, as env /simulate and the train data builder already do.
- 2026-10-18: The agent silences INFO request logs from `httpx2`/`httpcore2` (the openai package's transport since 3.x) as it did for `httpx`, so outgoing requests no longer fill stdout and Sentry Logs.

## Person 3

- 2026-10-18: D-03 confirmed by the live P3-05 smoke test (train 80 s / $0.31, save, attach in 72-88 s, adapter tool call OK). Veri names are lowercase: registry version `D-s0-g1` is served as `d-s0-g1` (`endpoint_model_id`).
- 2026-10-18: Gate retries runs that end in model_error, tool_error or timeout up to 2 more rounds before scoring them as failed (the shared L4 replica times out about 5% of turns at 16 concurrent runs). Generation 0 base score on eval_v1: 0.45.
- 2026-10-18: D-04: base model `Qwen/Qwen3-4B-Instruct-2507` (Qwen3.5-4B cannot be trained on Veri; see docs/VERI.md section 3).
- 2026-10-18: D-03: train with Veri managed `sft_text` (LoRA) on one prompt/completion row per assistant turn, serve base and all LoRA versions from one Veri multi-adapter deployment; SPEC 9.8 amended (pending Person 2's review of the PR); fallback is the custom script plus our own vLLM box.
- 2026-10-18: Person 1 is also covering Person 3's work package at the user's request; scar_schemas was bootstrapped from SPEC Section 8 and merged.
- 2026-10-18: `scripts/init_db.sh` applies the DDL and then `REVOKE EXECUTE ON FUNCTION pg_catalog.set_config(...) FROM PUBLIC` (sql_query isolation; env also applies it at startup), so `ddl.sql` stays verbatim from SPEC 8.6-8.7.
- 2026-10-18: The train service uses Fake trainer/deployer backends (the candidate is the base model) only for tests and tiny smoke runs until the Veri backend exists; fake results are never reported.
- 2026-10-18: Data builder replay uses traffic runs of generations <= g (SPEC says "earlier"; strictly earlier would give generation 1 no replay), one run per task (earliest), skipping runs the lineage already trained on.
- 2026-10-18: Data builder drops duplicates per task before applying MAX_EXAMPLES_PER_TASK; sampling and regression/eval runs that end in model_error, tool_error or timeout do not count, and an all-infrastructure-failure batch is a DependencyError.
- 2026-10-18: `export_jsonl` keeps key order (chat templates print the `tools` JSON in stored order, which must match what the agent sends).
- 2026-10-18: Gate: eval runs are reused across lineages per (task, model_version, prompt_version, purpose eval, temperature 0), so generation 0's base score is computed once; regression tasks come from the parent's most recent traffic generation up to the gated one; an empty regression set scores 1.0.
- 2026-10-18: Gate rule changed with the user's approval (SPEC 9.9, schemas 1.3.0): promote if eval beats the parent and the regression score is at least the parent's own regression score on the same set minus `REGRESSION_TOLERANCE` (0.05), with the parent re-run in the same gate.
The absolute `REGRESSION_MIN_PASS = 0.95` was unreachable: `base` re-passed only 81 of the 90 tasks it had passed in temperature-0 traffic (D-s0-g1's set).
D-s0-g1 was rejected under the old rule (eval 0.71 vs 0.45, regression 0.867) and is re-gated under the new one; B-s0-g1 (eval 0.083) fails the eval condition under either rule and keeps its record.
- 2026-10-18: Lineages take turns on the shared replica: agent batches and traffic simulation hold one process-wide lock in the train service. Two overlapping gates timed out about half their model calls.
