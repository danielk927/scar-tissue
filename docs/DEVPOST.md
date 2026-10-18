# Devpost draft (P1-15)

Draft text for the Devpost page.
Numbers come from `docs/RESULTS.md` (seed 0, generated from the experiment database).

## Tagline

An accounting agent that turns its own failures into training data and only ships a new model when it beats the old one on companies it has never seen.

## Track and challenges

- Main track: Model Learning.
- Sentry: AI agent monitoring and tracing on every agent run, Sentry Logs from all three services, Session Replay on the judge UI.
- Veri: every weight update is a Veri LoRA fine-tuning job, served from one Veri multi-adapter deployment.
- QuickBooks: Jev (a System One model) triages every failed run from the loop's practice traffic and from judges' questions when a generation mines its failures, and the agent works on the QuickBooks hackathon dataset.

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

- Base model: `Qwen/Qwen3-4B-Instruct-2507`, served by Veri with tool calling; it starts at 46.3% on the held-out eval set.
- Ledger: the QuickBooks dataset mapped into Postgres (159,790 expense rows across 100 companies, reconciled to each company's profit-and-loss report to the cent), with row-level security so the agent's SQL tool sees one company at a time.
- Tasks: 12 templates in three families (aggregation, anomaly, bookkeeping); every expected answer comes from SQL, never from an LLM.
- Services: an env service (ledger, tools, verifier, task generation), an agent service (agent loop, Sentry spans, Jev mining, judge flow) and a train service (data builder, Veri jobs, promotion gate, loop runner), all FastAPI over one Postgres.
- Comparison arms on identical traffic: a frozen base model, failure memory (lessons appended to the prompt), GEPA prompt optimization, and weight updates.

## Results

![Eval success rate by generation](results.png)

| Generation | Arm | Held-out eval | Parent eval | Regression | Parent regression | Gate |
|---:|---|---:|---:|---:|---:|---|
| 1 | Failure memory (lessons in the prompt) | 8.3% | 46.3% | 8.1% | - | rejected |
| 1 | GEPA prompt optimization | 43.0% | 46.3% | 90.1% | 96.7% | rejected |
| 1 | Weight updates (ours) | 70.7% | 46.3% | 86.7% | 93.3% | rejected |
| 2 | Weight updates (ours) | 72.7% | 46.3% | 92.7% | 88.5% | promoted |

After two generations, the weight-update arm answers 72.7% of questions about 30 companies it never trained on, up from 46.3% for the frozen base.
Both prompt-only arms made the agent worse after one generation.
The gate rejected our own first adapter even though it scored 70.7%: it broke 9 regression tasks the base solved and fixed only 3, below the base's own re-run by more than the 5-point tolerance.
The second adapter trained on both generations' data, beat the base on the regression set too (92.7% vs 88.5%), and shipped.
Gains by family for the first adapter: aggregation 29% to 79%, bookkeeping 61% to 79%, anomaly 49% to 55%.

## Challenges we ran into

- With our first system prompt the base model wrote `submit_answer(number=...)` as plain text instead of calling the tool and passed 0 of 30 seed tasks; describing the tool call in JSON terms took it to 12 of 30.
- Our SQL tool's row-level security could be escaped by calling `set_config` from inside a query, so we revoke that function from the tool's role.
- Veri's managed fine-tuning trains on whole conversations by default, so we export one training row per assistant turn to keep the loss on the model's own tokens and match the exact text vLLM shows the model.
- One GPU serves every agent run, so we scaled the loop (fewer samples and variants per failure) and retry runs that time out on the shared replica before they count against a model.
  When two gates ran at once, half the model calls timed out, so lineages now take turns on the replica one batch at a time.
- Our first "no forgetting" rule required 95% on tasks the parent had already passed, and the base model itself re-passed only 90% of them at temperature 0.
  The gate now re-runs the parent on the same tasks and compares the candidate with that score.
- Veri ran out of H100 capacity mid-run, so a training job that cannot be placed moves to the same GPU at another provider, then to an A100.

## What we learned

- A 4B model learns tool-calling habits from a few hundred of its own passing transcripts: the first adapter stopped writing `submit_answer(...)` as text and gained 50 points on aggregation questions.
- Prompt-only memory can backfire on a small model: lessons written by a larger model quoted `submit_answer({"number": X})` as text, and the 4B model copied the text instead of calling the tool.
- A referee only means something if it can say no to you; ours rejected our best-scoring adapter, and the next one fixed the regression.
- Measure noise before you set a threshold: the same model at temperature 0 swung by 3 of 90 tasks between two runs.

## Data changes (QuickBooks disclosure)

We load only expense activity (bill lines and expense journal lines); income, payments, transfers and the bank feed stay out.
We added one "Prepaid Expenses" account to every company so the prepaid-amortization task can run.
We planted 989 duplicate bill payments (5 to 15 per company) so duplicate-payment questions always have answers.
The full list, with every planted row, is in `docs/DATASET_CHANGES.md`.

## Gallery

- `docs/results.png`: eval success rate by generation, with rejected candidates.
- `docs/screenshots/veri-job-d-s0-g2.png` and `veri-job-d-s0-g1.png`: the two Veri LoRA jobs (the promoted one and the one the gate rejected).
- `docs/screenshots/veri-deployment.png`: the Veri multi-adapter deployment that served every agent run (25,967 requests).
- `docs/screenshots/sentry-agents.png`, `sentry-judge-trace.png`, `sentry-issues.png`: Sentry AI agent monitoring, a judge question's trace, and failure issues.

## Built with

Python, FastAPI, PostgreSQL, Next.js, Sentry, Veri, Jev (TypeSafe), vLLM, Qwen3-4B-Instruct-2507, GEPA, Groq, uv.
