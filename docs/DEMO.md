# Demo

Owner: Person 2 (script, question list, Sentry views). Person 3 adds the demo-generation procedure under its own heading.
Spec: SPEC 14 (about 4 minutes). Deadline: Devpost 12:00 PDT Oct 18; demo freeze about 08:00 PDT.

## Setup (30 minutes before judging)

1. **Warm the model.** `base` parks after 15 idle minutes and takes about 15 to wake: `cd train && uv run python -m scar_train.veri wake`, then ask one test question.
2. **Services on the demo laptop** (Person 1's, which has the experiment database): env on `:8001`, agent on `:8002`, train on `:8003`. Check each `/health`.
3. **Web app**, pointed at those services. No rebuild is needed, because URLs are read per request:
   `cd web && AGENT_SERVICE_URL=http://<host>:8002 TRAIN_SERVICE_URL=http://<host>:8003 ENV_SERVICE_URL=http://<host>:8001 npm run dev`
   Set `NEXT_PUBLIC_SENTRY_REPLAY_RATE=1` in `.env` before starting it (a `NEXT_PUBLIC_*` change needs a restart).
4. **Judge parsing** needs, in the demo laptop's `.env`: `PARSE_LM_*` set to Groq (the same values as `REFLECTION_LM_*`), `TYPESAFE_API_KEY`, and `MODEL_API_KEY`.
5. **Browser tabs, in this order:** `/dashboard`, `/ask`, the Sentry issues view, the Sentry AI Agents Overview, one prepared trace (links below).
6. Confirm `GET :8003/models/current?arm=D&seed=0` returns the newest promoted D-s0 model. The judge's runs use it.

## Solo script (one presenter, about 4 minutes)

Use this when one person presents; the three-person script below has the same beats.
Tabs, in order: `/dashboard`, `/ask`, the mined failure `/runs/run_0faba9d7e7704101a0f6e6593a6ba91c`, the Sentry issues view, the Sentry AI Agents Overview, the D-s0-g2 Veri job (https://veri.studio/training?job=d1a57653ef1b).

| Time | Screen | Say |
|---|---|---|
| 0:00-0:20 | `/dashboard`, chart | "Most self-improving agents only rewrite their prompts. Ours turns every failure Sentry catches into training data, retrains the model on Veri, and only ships it if it gets better on companies it has never seen." |
| 0:20-0:50 | `/dashboard`, per-arm table | "This is a 4-billion-parameter Qwen model doing bookkeeping on the QuickBooks dataset. On 30 companies it never trained on, the base model answers 46.3%. After two rounds of retraining on its own failures, 72.7%. On the same failures, lessons in the prompt dropped it to 8.3%, and GEPA to 43%." |
| 0:50-1:10 | `/dashboard`, generations table, D-s0-g1 row | "The referee is strict. Our first adapter scored 70.7%, but it broke tasks the base already solved: 86.7% against the base's 93.3% on the same tasks. It didn't ship. The second one also beat the base there, and shipped." |
| 1:10-2:10 | `/ask` | Hand over the laptop, or type a question from "Prepared questions, re-run on D-s0-g2" below. Click **Ask** and read the interpretation: "Jev picks the question type, a parse model fills in the real account and dates, and code computes the expected answer from the ledger. No LLM grades it." Click **Yes, run it**. While it runs (10 to 60 s), open the Sentry AI Agents Overview: "every run is traced." Back on `/ask`, read the result card and the verifier line. |
| 2:10-2:40 | `/runs/run_0faba9d7e7704101a0f6e6593a6ba91c`, then Sentry issues | "When a run fails, Sentry keeps the trace and groups the failure. Jev triages it: this one gave up without answering, and Jev rates it 93% learnable, so it became training data." |
| 2:40-3:20 | Veri job page | "That one failure became five new practice questions from the same template at other companies, with answers computed by SQL. The model practiced on them, we kept only its passing attempts, and Veri fine-tuned a LoRA adapter on them: 405 examples across two generations, served from one multi-adapter deployment." |
| 3:20-4:00 | `/dashboard` | "Weight updates beat prompt-only methods on the same failures, and the gate refused a model that only looked better. Sentry gives us the traces, failure issues and replays; Veri runs every training job and serves every version; the QuickBooks dataset and Jev give us the ledger and the triage." |

If the live question takes over 2 minutes, keep talking over the Sentry tabs, then open a prepared run.

## Demo video (2:30, recorded)

Record with QuickTime (File → New Screen Recording) at 1920x1080 with the browser full screen, then upload to YouTube as unlisted.
Use prepared runs instead of waiting on a live one.
Wake the model first (Setup, step 1).

| # | Time | Shot | Narration |
|---|---|---|---|
| 1 | 0:00-0:15 | `/dashboard` chart | The hook from the solo script, 0:00-0:20. |
| 2 | 0:15-0:40 | `/dashboard` per-arm table, then the generations table | 46.3% to 72.7% on held-out companies; lessons-in-prompt 8.3%, GEPA 43%; D-s0-g1 rejected at 70.7% for breaking solved tasks. |
| 3 | 0:40-1:15 | `/ask`: prepared question #1 (Riverstage Arts Alliance, count of Operating Expense transactions), **Ask**, the interpretation, **Yes, run it**, the result card | "A judge asks a question about one company's books. The model that failed this last night now gets it right." |
| 4 | 1:15-1:35 | The base model's run of the same question (for #1: `/runs/run_f0c366eddbc14ad98ba942bcb84b4f26`) | "Here is the base model on the same question: it never submitted an answer." |
| 5 | 1:35-1:55 | `/runs/run_0faba9d7e7704101a0f6e6593a6ba91c`, then the Sentry trace and issues | Failures are traced in Sentry and triaged by Jev into training data. |
| 6 | 1:55-2:15 | The Veri job page | Five variants per failure, passing attempts only, one Veri LoRA job per generation. |
| 7 | 2:15-2:30 | `/dashboard` chart | The close from the solo script, shortened. |

## Script (SPEC 14; Person 2 drives 1:00–2:30)

| Time | Who | Clicks and words |
|---|---|---|
| 0:00–0:20 | Person 1 | Hook (SPEC 14). |
| 0:20–1:00 | Person 3 | **/dashboard**: eval score by generation per arm, mean with a min–max band over seeds; hover a generation for the per-arm tooltip. Point at a rejected row in the generations table: "the gate didn't ship it." |
| 1:00–1:15 | Person 2 | **/ask**: hand the judge the laptop. "Pick a company, ask anything about its books." If they're stuck, offer a question from the table below. |
| 1:15–1:30 | Person 2 | Click **Ask**. Parsing takes 1–15 s (Jev picks the template, the parse model fills in real IDs). Read the interpretation aloud; the expected answer is never shown before the run. Click **Yes, run it**. |
| 1:30–2:00 | Person 2 | While it runs (30 s to 3 min), switch to the **Sentry AI Agents Overview** tab: "every run is traced; this is all of tonight's traffic." |
| 2:00–2:20 | Person 2 | Back on /ask, the result card shows **Failed**, the agent's answer and the verifier line. Click **Open the full run** for the transcript, then **Open Sentry trace**: the waterfall of model and tool calls, the env service's spans inside the same trace, and the Agent Activity transcript. |
| 2:20–2:30 | Person 2 | Open the Sentry **Issues** tab: the failure joined an `agent_run_failed` issue for its template and failure mode. "This is the failure Sentry caught. Jev triages it, and it becomes training data for the next generation." |
| 2:30–3:20 | Person 3 | The generation record, variants, the Veri job and the gate scores; then **Re-run on newest model** on an earlier judge question that the new generation fixed. |
| 3:20–4:00 | Person 1 | Close (SPEC 14). |

### Fallbacks

- **The run takes too long (over about 2 min):** keep talking over the Sentry tabs. At 3 min it ends as `timeout`; open a prepared failed run instead (`/runs/<run_id>`, on the laptop whose database has it).
- **The model is asleep or returns 503:** show a prepared trace and run page, and run `veri wake` in the background.
- **Parse fails with an error (HTTP 503):** click **Ask** again; it happened once in ten parses at 09:25 PDT.
- **Parse says it can't answer:** the card offers three example questions for that company. Use one, or a question from the table.
- **The judge's question passes:** that's fine ("base gets some right"); then run a prepared failing question.
- **Groq is rate-limited (parse over 15 s):** wait; it retries on its own. Don't click **Ask** twice.
- **Wi-Fi is down:** use the screenshots in `docs/screenshots/` and Person 3's backup recording.

## Prepared questions (P2-11)

These ten questions are all on train companies. On 2026-10-18, **base failed every one of them through the live judge flow** on Person 2's local stack, which has the PR #2 prompt and ledger checksums identical to Person 1's. Each parsed to the listed template with Jev confidence of at least 0.99. None failed for infrastructure reasons (no `model_error` or `timeout`), so each is a learnable failure that mining includes.

Spare: #13 from the handoff list (Courtyard Commons Apartments Construction, Taxes and Licenses total from 2025-10-08 to 2026-09-15, `agg.total_by_account_period`). Base runs at temperature 0 but isn't perfectly deterministic: in the same session, two other candidates (#18 and #22) passed, and three (#15, #21 and #24) failed for infrastructure reasons.

| # | Company | Question (type it as is) | Template | How base failed | Run (local) |
|---|---|---|---|---|---|
| 1 | Riverstage Arts Alliance (`company-099`) | Count the Operating Expense transactions dated between 2025-10-31 and 2025-12-07 (both dates included). | `agg.count_by_account_period` | never called submit_answer (wrote the answer as text) | `run_5e146646a58e4d6db34503493ca0fef8` |
| 2 | Brightwater Community Fund (`company-097`) | How much more was recorded in Insurance in May 2026 than in April 2026? Answer with a negative number if it went down. | `agg.mom_change_account` | never called submit_answer (wrote the answer as text) | `run_ac0d5376c445487b8d824207452d9af7` |
| 3 | Riverstage Arts Alliance (`company-099`) | What percentage of our total expenses was Rent and Lease from 2025-12-01 through 2026-10-03, inclusive? Use every expense account for the total and round to two decimal places. | `agg.share_of_expense` | never called submit_answer (wrote the answer as text) | `run_bb66f98c02054f4ca6e5289327d89f62` |
| 4 | Brightface Sign Co (`company-053`) | Which vendor did we spend the most with between March 3 and April 22, 2026, inclusive? If several vendors tie for the top spot, list all of them. | `agg.top_vendor_by_spend` | never called submit_answer (wrote the answer as text) | `run_719a227dd6bb428483bd0a2a1327db7c` |
| 5 | Oddjob Specialty Trades & Supply (`company-051`) | Add up every Taxes and Licenses transaction dated 2026-07-25 through 2026-10-06. What is the total? | `agg.total_by_account_period` | never called submit_answer (wrote the answer as text) | `run_04b2ab43f76440cdb18160c9f2e78bd3` |
| 6 | Birchfield Custom Homes (`company-029`) | From 2026-07-07 to 2026-07-20 inclusive, what was our total spend with the vendor Copper Sitework Services? | `agg.total_by_vendor_period` | wrong number (expected 16972.42, got 16972.39) | `run_39fb2dacb95d4817a678cc01e5a59200` |
| 7 | Copper Kettle Cafe (`company-073`) | Book a journal entry dated February 28, 2026 accruing $4,178.47 of unpaid Project Manager Wages to Wages Payable. | `book.accrual` | wrong journal entry (matched 0 of 1 expected entries) | `run_ce0e9c68480a4e838ed0c96a63ddcb0e` |
| 8 | Signalcraft Systems Integration (`company-054`) | Set up 2 month-end journal entries, starting January 2026, that amortize $20,378.78 of Prepaid Expenses into Operating Expense. Split it evenly, rounded to cents, with any leftover cents in the final month. | `book.prepaid_amortization` | wrong journal entries (matched 0 of 2 expected entries) | `run_c3199645aa114b929b074841bf83ccbe` |
| 9 | Signalcraft Systems Integration (`company-054`) | Transaction company-054:pbill-00392:1 belongs in Subcontractors. Post one journal entry on its date to reclassify the whole amount there. | `book.reclassify` | never called submit_answer (wrote the answer as text) | `run_83fe3203723f40a09eab94a8177d1fa3` |
| 10 | Lanternwade Commercial Realty (`company-067`) | Transaction company-067:bill-software-00238:1 belongs in Insurance. Post one journal entry on its date to reclassify the whole amount there. | `book.reclassify` | recorded entries but submitted no entry_ids | `run_9fa3f27c37054a0491076ed440a85e6b` |

### Prepared questions, re-run on D-s0-g2

Run on Person 1's laptop at 09:25 PDT through `/judge/parse` and `/judge/run` (the judge's runs use the current model, D-s0-g2), and on `base` for the same tasks (`purpose="smoke"`).
D-s0-g2 passed 7 of 10 and `base` 2 of 10; D-s0-g2 fixed #1, #3, #4, #7 and #8.
For the video and the solo script, ask a **fixed** question live and open the base run next to it; ask a question that fails on both when you want to show a failure being caught.
Run pages are at `/runs/<run_id>` on Person 1's laptop. Outcomes at temperature 0 can still vary between runs.

| # | Use | D-s0-g2 | D-s0-g2 run | base | base run |
|---|---|---|---|---|---|
| 1 | **fixed** | passed | `run_1be7b35b549448efac84554709030327` | failed (no answer submitted) | `run_f0c366eddbc14ad98ba942bcb84b4f26` |
| 2 | passes on both | passed | `run_4cbc3973c1434fcb837da1df12e29352` | passed | `run_ae46f776d73c47c8a330215d08d0db4c` |
| 3 | **fixed** | passed | `run_72bcf1b9eed641309f505adc18d810b1` | failed (no answer submitted) | `run_2e89b9e2c6ce48a19bbbe17e0977b570` |
| 4 | **fixed** | passed | `run_71d29160f2d943acb047f8d8f9963814` | failed (no answer submitted) | `run_5fa07192eb8c4c42b2c04c444a82df8c` |
| 5 | passes on both | passed | `run_0b9d1b0f2a0c4acaad2e485f846dff73` | passed | `run_75c815eb82194a22becbada93e24676a` |
| 6 | fails on both | failed (wrong answer) | `run_5aab649f034244098d0d14e28f55293f` | failed (wrong answer) | `run_24b1a7d8d6174d70a2906b433be2beb5` |
| 7 | **fixed** | passed | `run_14464e1a2d514039a85c1efda4ea78df` | failed (wrong answer) | `run_3439f52dd0ce4e24a30b27a4150b4eed` |
| 8 | **fixed** | passed | `run_5dba07ca183e413ebc1271f4d4f056c0` | failed (wrong answer) | `run_1b3ad5bb6fc543789f110ef2fe2d66dd` |
| 9 | fails on both | failed (wrong answer) | `run_e656d02686574c06bf6f9c3cb991b88a` | failed (no answer submitted) | `run_8ca83495b13d4fb98134b02facdc04b0` |
| 10 | fails on both | failed (wrong answer) | `run_3e54470464094535b7d40f374d96b747` | failed (wrong answer) | `run_4aff1ced532e4d85bbf1148d9c4b2a8b` |

## Sentry views (P2-10)

Org: https://ucla-s1.sentry.io

- Issues, grouped `agent_run_failed`: https://ucla-s1.sentry.io/issues/?groupStatsPeriod=auto&query=agent_run_failed&referrer=issue-list&sort=recommended&statsPeriod=14d
- AI Agents Overview dashboard: https://ucla-s1.sentry.io/dashboard/10476398/
- One failed traffic trace: https://ucla-s1.sentry.io/explore/traces/trace/5ce7b14a306d42cf929a9ce946b3d9fc/ (no_submit; agent and env spans in one trace)

Person 2 checked these on 2026-10-18:
- Logs from the env, train and agent services arrive in Explore → Logs.
- Failed traffic runs group into one `agent_run_failed` issue per (template, stop reason).
- Traces show agent and env spans in one trace.
- The AI Agents Overview dashboard shows runs, tokens and tool calls.

Session Replay: confirmed 2026-10-18; Person 2's judge session (Windows, Chrome) appears in Session Replay. A judge click links to its backend run: that session's trace has the browser's `GET /ask` at the root and the `ledger-agent` run inside it (`sentry-judge-trace.png`).

Two notes for the services:
- The agent no longer sends its outgoing HTTP request logs (fixed 2026-10-18).
- The env service still sends one uvicorn access log line per tool call, which is most of the log volume. Person 1 can filter it if it matters.

## Screenshots (SPEC 17)

In `docs/screenshots/`:

| File | Shows |
|---|---|
| `sentry-issues.png` | `agent_run_failed` issues, one per (template, stop reason) |
| `sentry-issue-detail.png` | One `agent_run_failed` issue: 24 events, `warning` level, linked to its run trace |
| `sentry-judge-trace.png` | A judge question's trace: the root is the browser's `GET /ask`, with the `ledger-agent` run nested inside (Agent Activity timeline: chat, `get_accounts`, `get_transactions` with the parsed dates, the system prompt and the judge's question) |
| `sentry-agents.png` | The AI Agents Overview dashboard: agent runs, LLM calls by model, tokens, tool calls |
| `sentry-agent-traces.png` | The dashboard's per-run trace table: duration, errors, LLM calls, tool calls, tokens |
| `sentry-replays.png` | The Session Replay list with Person 2's live judge session |
| `app-judge-result.png` | `/ask`: a question on Ashgrove Family Law, answered 57000 against an expected 56999.95, marked Failed |
| `app-run-viewer.png` | `/runs/<id>`: outcome, verifier detail, Sentry trace link, Re-run button, transcript |

Optional, if time allows: the replay player paused on the `/ask` result card (`sentry-replay.png`).

## Rehearsals

| # | When | Who | Total time | What went wrong | Fix |
|---|---|---|---|---|---|
| 1 | | | | | |
| 2 | | | | | |

Notes for the team: rehearse twice before 08:00 PDT; time the 1:00–2:30 segment with a real judge-style question; decide who clicks and who talks.

## Person 3: demo generation procedure

State at the freeze (07:15 PDT): no more loop runs.
`D-s0` serves `D-s0-g2` (`GET :8003/models/current?arm=D&seed=0`), attached to deployment `5a6d7ab804e0` as `d-s0-g2` next to `scar-base` and the rejected `d-s0-g1`.
Numbers and every candidate are in `docs/RESULTS.md`.

### Keep it warm

The deployment parks after 15 idle minutes and takes about 15 minutes to wake, and every adapter parks with it.
Run `cd train && uv run python -m scar_train.veri wake` 30 minutes before judging, then ask one question on `/ask` so both the base and `d-s0-g2` have answered once.
During the judging window, keep it up from a second terminal: `cd train && uv run python -m scar_train.keep_warm --minutes 120`.
It follows the current D-s0 model and sends one tool-call check every two minutes; stop it with Ctrl-C after judging so the deployment can park.
It bills $1.05 per hour while awake; the Veri balance was $10.17 at the freeze.

### What to show in 2:30-3:20

1. **/dashboard generations table**: D-s0-g1 scored 70.7% on held-out companies but was **rejected**: it broke 9 regression tasks the base solved and fixed 3 (86.7% vs the base's 93.3% on the same set, more than the 5-point tolerance). D-s0-g2, trained on both generations' 405 examples, scored **72.7% vs 46.3%** and beat the base on regression too (92.7% vs 88.5%), so it shipped.
2. **The Veri jobs**: D-s0-g1 https://veri.studio/training?job=1b079d04fb2a and D-s0-g2 https://veri.studio/training?job=d1a57653ef1b (one LoRA job each, `sft_text`, one row per assistant turn).
   Screenshots, if the dashboard is slow: `docs/screenshots/veri-job-d-s0-g1.png` (H100, 2,038 steps), `docs/screenshots/veri-job-d-s0-g2.png` (A100, 3,348 steps, LoRA rank 16, 2 epochs) and `docs/screenshots/veri-deployment.png` (the multi-adapter deployment: 25,967 requests, $7.19 over 6.9 hours).
   If a judge asks about D-s0-g2's final loss of 0.0000: it is the loss on the last training row; the gate scores on 30 companies the model never trained on (72.7% vs 46.3%).
3. **Fixed on held-out companies**: on `eval_v1`, D-s0-g2 passes 96 tasks the base failed and fails 17 the base passed. Open a pair side by side (`/runs/<run_id>`; these run pages exist only on Person 1's laptop, which has the database):

| Family | Question | Base (failed) | D-s0-g2 (passed) |
|---|---|---|---|
| aggregation | From 2026-10-01 to 2026-10-05 inclusive, what did Insurance transactions sum to? (company-083) | `run_cb1fbdf689924dfb9b977bd35d17153c` (no submit) | `run_ee56cb4faf2f4f6e8ff47109ef193db3` (8 s) |
| aggregation | What is the number of transactions posted to the Wage Expense account from 2025-10-20 through 2026-03-30? (company-022) | `run_980499c1128841af91fed8417163a3fa` (no submit) | `run_f23370ba348e4d7a9db1b57603bf79c1` (8 s) |
| anomaly | From 2026-09-06 to 2026-09-16 inclusive, flag all transactions of $200.00 or more whose amounts are exact multiples of $100. (company-017) | `run_fc94fc9088d64a33a6c82ea6722c6d07` (wrong IDs) | `run_892d5280f40d4f1caf11e5f3b6d85bb9` (23 s) |
| bookkeeping | We owe $3,766.97 for Interest Expense that has not been billed yet. Accrue it on 2026-03-31 against Accrued Interest. (company-022) | `run_ab8cf5c38fc745b99134b2731fff730c` (wrong entry) | `run_33f73c7bc7fb4c94a42a2990db14574f` (14 s) |

4. **Re-run on newest model** on a judge question runs `D-s0-g2`. It is not guaranteed to pass (it fails 27% of held-out tasks); if it fails, fall back to the table above.

### A live generation (not during judging)

A full generation took 1.5 to 2.5 hours on one replica, so none runs during judging.
Measured on D-s0-g2: traffic 11 min, mining and variants under 1 min, sampling the variants 32 min, Veri training 45 min (A100; D-s0-g1 took about 18 min on an H100), attaching the adapter 1 to 15 min, gate 29 min.
A tiny generation on its own lineage (`SEED=9 scripts/smoke_ic2.sh`, with `TRAINER=veri`) runs every step on a few tasks, but it still waits for a Veri training slot (3 to 4 minutes queued in the P3-05 smoke) and it shares the replica with the judge's runs.
If a judge asks for a live cycle, show the D-s0-g2 records and Veri job instead.
After judging, `cd train && uv run python -m scar_train.campaign --demo --plan-only` shows the next generation (D-s0-g3) and `--demo --max-hours 2` runs it.
