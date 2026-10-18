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
