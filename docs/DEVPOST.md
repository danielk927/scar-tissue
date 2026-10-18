# Devpost draft (P1-15)

Draft text for the Devpost page.
Fill the `RESULT:` placeholders from `docs/RESULTS.md` once the generations finish.

## Tagline

An accounting agent that turns its own failures into training data and only ships a new model when it beats the old one on companies it has never seen.

## Track and challenges

- Main track: Model Learning.
- Sentry: AI agent monitoring and tracing on every agent run, Sentry Logs from all three services, Session Replay on the judge UI.
- Veri: every weight update is a Veri LoRA fine-tuning job, served from one Veri multi-adapter deployment.
- QuickBooks: Jev (a System One model) triages every failed run in real time, and the agent works on the QuickBooks hackathon dataset.

## Inspiration

Most self-improving agents rewrite their prompt and stop there.
We wanted to see what happens when an agent's mistakes change its weights, with a referee that refuses to ship a model that only got better at the questions it practiced.

## What it does

You ask the agent a bookkeeping question about one of 70 synthetic companies, such as total spend in an account, duplicate payments, or an accrual entry.
The agent answers with tools that only see that company's ledger, and code checks the answer against a value computed from the ledger.
When a run fails, Sentry records the trace and opens an issue, and Jev decides why the run failed and whether a better model could have avoided it.
Each avoidable failure becomes new practice tasks from the same template, with fresh parameters and answers computed by SQL.
The model practices on those tasks, we keep only its passing attempts, and Veri fine-tunes a new LoRA adapter on them.
The new version ships only if it beats its parent on 300 held-out questions from 30 companies it never trained on, without forgetting what it already solved.
Then the loop runs again.

## How we built it

- Base model: `Qwen/Qwen3-4B-Instruct-2507`, served by Veri with tool calling; it starts at 45% on the held-out eval set.
- Ledger: the QuickBooks dataset mapped into Postgres (159,790 expense rows across 100 companies, reconciled to each company's profit-and-loss report to the cent), with row-level security so the agent's SQL tool sees one company at a time.
- Tasks: 12 templates in three families (aggregation, anomaly, bookkeeping); every expected answer comes from SQL, never from an LLM.
- Services: an env service (ledger, tools, verifier, task generation), an agent service (agent loop, Sentry spans, Jev mining, judge flow) and a train service (data builder, Veri jobs, promotion gate, loop runner), all FastAPI over one Postgres.
- Comparison arms on identical traffic: a frozen base model, failure memory (lessons appended to the prompt), GEPA prompt optimization, and weight updates.

## Results

RESULT: eval score per generation for each arm (chart from `docs/RESULTS.md`).
RESULT: one sentence on the headline comparison.

## Challenges we ran into

- With our first system prompt the base model wrote `submit_answer(number=...)` as plain text instead of calling the tool and passed 0 of 30 seed tasks; describing the tool call in JSON terms took it to 12 of 30.
- Our SQL tool's row-level security could be escaped by calling `set_config` from inside a query, so we revoke that function from the tool's role.
- Veri's managed fine-tuning trains on whole conversations by default, so we export one training row per assistant turn to keep the loss on the model's own tokens and match the exact text vLLM shows the model.
- One GPU serves every agent run, so we scaled the loop (fewer samples and variants per failure) and retry runs that time out on the shared replica before they count against a model.

## What we learned

RESULT: to fill in after the runs.

## Data changes (QuickBooks disclosure)

We load only expense activity (bill lines and expense journal lines); income, payments, transfers and the bank feed stay out.
We added one "Prepaid Expenses" account to every company so the prepaid-amortization task can run.
We planted 989 duplicate bill payments (5 to 15 per company) so duplicate-payment questions always have answers.
The full list, with every planted row, is in `docs/DATASET_CHANGES.md`.

## Built with

Python, FastAPI, PostgreSQL, Next.js, Sentry, Veri, Jev (TypeSafe), vLLM, Qwen3-4B-Instruct-2507, GEPA, Groq, uv.
