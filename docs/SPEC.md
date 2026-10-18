# Scar Tissue — Project Spec v1.0

**An agent that retrains itself from its own failures**

LA Hacks AI Hackathon 2026 · October 17–18 · James West Alumni Center, UCLA

| Person | Name | Area |
|---|---|---|
| Person 1 | ________ | Environment + experiments |
| Person 2 | ________ | Agent + observability + web |
| Person 3 | ________ | Training + loop |

**Status: SOURCE OF TRUTH.** Spec version 1.0.0. Commit this file to the repo as `docs/SPEC.md`. If code and this spec disagree, the spec wins until the spec is changed through the change protocol in Section 8.9.

---

## How to use this document

**Humans:** read Sections 1–10 once, all the way through. Then read your own work package (Section 11, 12, or 13) in detail. Sections 14–17 are for the demo, risks, decisions, and submission.

**AI coding assistants:** your human will give you this whole file plus a briefing prompt from Appendix A. Read Section 1 first and follow it strictly. Everything you need to build your part without coordinating in real time is in Sections 6–9 (architecture, conventions, contracts, algorithms) and your person's work package.

### Contents

1. Rules for every AI assistant
2. Project summary
3. Glossary
4. Verified facts vs. open questions
5. Prizes and how we qualify
6. System architecture
7. Tech stack, repo layout, and conventions
8. Shared contracts (schemas, tools, APIs, database)
9. Algorithms, precisely
10. Phases and integration checkpoints
11. Work package: Person 1
12. Work package: Person 2
13. Work package: Person 3
14. Demo script
15. Risks and fallbacks
16. Decision log
17. Submission checklist
- Appendix A: briefing prompts for each person's AI assistant
- Appendix B: references

---

## 1. Rules for every AI assistant (read first)

These rules exist so three people and three AI assistants can work in parallel without conflicts or invented details. They are numbered so humans can point to them ("that breaks R4").

**R1. This spec is the source of truth.** Do not contradict it. If something here seems wrong, stop and tell your human; do not silently "fix" it in code.

**R2. Stay inside your ownership.** Only create or edit files in the paths your person owns (Section 7.2). Only write to database tables your person owns (Section 8.6). Any service may *read* any table. If you need a change in someone else's area, write it down as a request for your human to pass on.

**R3. Shared contracts are frozen.** The models, constants, tool definitions, API shapes, and database tables in Section 8 must be used exactly as written. Import them from the `scar_schemas` package; never redefine or copy them. Changing them requires the protocol in Section 8.9.

**R4. Never invent external APIs.** Section 4 lists what has been verified about Jev, Sentry, Veri, vLLM, GEPA, and the dataset. Anything marked **VERIFY** must be checked against the official docs linked in Appendix B before you write code against it. If you cannot verify it, put it behind a small adapter with the interface defined in this spec, make the unverified call fail loudly, add a comment `# TODO(VERIFY): <exactly what is unverified>`, and tell your human. Never guess a function name, parameter, environment variable, or URL.

**R5. No LLM ever produces a label.** Every expected answer is computed by code from the ledger. LLMs may write question wording, lessons, and prompts, never ground truth.

**R6. The eval split is untouchable.** Tasks from eval-split companies must never be used to make variants, training examples, GEPA training/validation sets, or memory lessons. Code paths that build any of those must assert this (Section 9.11).

**R7. Training data must match inference exactly.** A training example is the `messages` list from a passed `RunResult`, used verbatim, converted only with the shared functions in `scar_schemas.openai_format`. Never re-template or hand-edit transcripts.

**R8. Be deterministic.** Every random choice takes an explicit seed. Every SQL query that feeds sampling or ordering has an `ORDER BY`. Same inputs must give the same outputs.

**R9. Secrets live in `.env` only.** Never commit keys. Never print keys in logs.

**R10. Don't break the loop.** Before merging, run your own tests plus the current integration smoke test (Section 10). Never merge something that makes the smoke test fail.

**R11. When this spec is silent, choose the simplest option consistent with it,** and record the choice as one line in `docs/DECISIONS.md` under your person's heading.

**R12. No new vendors or services** (new APIs, databases, queues, frameworks) without your human agreeing with the team first.

---

## 2. Project summary

### 2.1 What we're building

An accounting agent that answers questions and does bookkeeping tasks over the QuickBooks hackathon dataset. Every run is recorded in our database and traced in Sentry. When a run fails, Jev classifies why. Failures the model could have avoided become batches of new training tasks whose answers are computed by code. We retrain the model on Veri, and promote the new version only if it beats the old one on companies it has never seen. Then the loop repeats.

**Pitch:** *"Most self-improving agents only rewrite their prompts. Ours turns every failure Sentry catches into training data, retrains the model, and only ships the new version if it gets better on data it has never seen."*

### 2.2 What's new, and what isn't

The basic loop from production failures to weight updates already exists. AgentScope with Trinity-RFT captures production logs, has an LLM judge label them, and kicks off fine-tuning automatically. At the spring 2026 LA Hacks, a project called Autopsy fed a coding agent's past failures into its next attempt, which changes what the agent is told but not the model. So we do not pitch "a loop that learns from failures" as the new part.

What we add is four things together:

1. The loop is built on Sentry's agent tracing, the sponsor's own product.
2. Jev triages every failure in real time so we only train on mistakes the model could have avoided. Running an LLM judge on every single run would be too slow and costly; Jev makes it cheap.
3. An honest comparison against prompt-only methods (failure memory and GEPA), with the same failures, held-out companies, and multiple seeds.
4. A live demo where judges' own failed questions become training data and get fixed during judging.

### 2.3 What success looks like

**Result:** a chart of eval success rate across generations for each arm (Section 9.10), averaged over seeds, where weight updates (arm D) beat the baselines by the last generation, or a clear crossover point where prompt methods lead early and weight updates take over. Either is a good story. A flat line is not.

**Demo:** a judge asks a question, it fails, we show the Sentry trace and Jev's diagnosis, and a later generation answers that exact question correctly.

### 2.4 Non-goals

We are not integrating with the real QuickBooks API, building user accounts or authentication, hardening for production security, using Fetch.ai/Agentverse, or building hardware. Do not build any of these.

---

## 3. Glossary

| Term | Meaning |
|---|---|
| **Ledger** | The accounting data, loaded into Postgres schema `ledger` in our canonical format (Section 8.7). Read-only to the agent except the sandbox. |
| **Company** | One business in the dataset. Every task belongs to exactly one company. |
| **Split** | Each company is assigned to `train` or `eval` once, deterministically (Section 9.1). |
| **Task** | One question or bookkeeping job with a code-computed expected answer. |
| **Template** | A parameterized task type (e.g. "total spend in account X between dates") with code that computes the answer. |
| **Task family** | `aggregation`, `anomaly`, or `bookkeeping`. |
| **Run** | One attempt by the agent at one task with one model version and one prompt version. |
| **Trajectory / transcript** | The full `messages` list of a run, including system prompt, tool calls, and tool results. |
| **Verifier** | Code that decides pass/fail for a run by comparing the submitted answer to the expected answer. |
| **Failure record** | Jev's triage of a failed run: cause plus probability the failure was learnable. |
| **Included failure** | A failure that passes the inclusion rule (Section 9.4) and may be learned from. |
| **Variant** | A new task generated from an included failure: same template, different parameters and wording. |
| **Arm** | One experimental condition (A–E, Section 9.10). |
| **Seed** | One independent repetition of an arm's whole loop (0, 1, 2). |
| **Generation** | One turn of the loop for an arm and seed. Generation 0 is the starting point; generations 1–4 are learning steps. |
| **Model version** | A specific set of weights at an endpoint (`base`, or `D-s0-g2`, etc.). |
| **Prompt version** | A specific system prompt (`system_v1`, or a memory/GEPA prompt). |
| **Promotion gate** | The check a new candidate must pass to replace the current version (Section 9.9). |
| **Registry** | The `app.model_versions` and `app.generations` tables. |
| **Replay** | Previously solved examples mixed into training so the model doesn't forget old skills. |
| **Teacher path** | (Stretch) Using a stronger model's successful runs for tasks our model never solves. |
| **IC-n** | Integration checkpoint n (Section 10). |

---

## 4. Verified facts vs. open questions

Legend: **VERIFIED** means confirmed from official docs or the hackathon pages. **VERIFY** means partially known; check the linked docs before relying on it. **UNKNOWN** means nobody has checked yet; the owner must find out.

| # | Item | What we know | Status | Owner |
|---|---|---|---|---|
| F1 | Event | Oct 17–18, 2026, James West Alumni Center, UCLA. Must be present in person for judging. | VERIFIED | all |
| F2 | Submission | Devpost page with description (naming track and company challenges), public GitHub repo, 2–3 minute demo video. Work must be done within the hackathon timeframe. Deadline Oct 18, 2026 12:00pm PDT; no code may be written after it; late submissions are not accepted. All coding happens during hacking (Sat 12 PM - Sun 12 PM); planning before is allowed. Exactly one main track (Model Learning, Beyond the Digital, Applied Intelligence) plus any number of company challenges. Max 4 per team. The Devpost `/rules` page still shows a stale spring date (Apr 26, 8am PDT); confirm with organizers. | VERIFIED | P1 |
| F3 | Sentry challenge | Use at least two products beyond error monitoring (Session Replay, Logs, Tracing, Profiling, Uptime Monitoring, MCP/AI agent monitoring) and show how observability shaped the build. Full rules not yet read. | VERIFIED (summary) / VERIFY (full) | P2 |
| F4 | Veri challenge | "Post-train / Fine-tune a model to be better at a task using Veri." Full rules not yet read. | VERIFIED (summary) / VERIFY (full) | P3 |
| F5 | QuickBooks challenge | Build something QuickBooks users couldn't get from classical ML or conventional GenAI using System One models like Jev (real-time analysis, smarter decisions, work that used to be too slow or costly). Synthetic data across multiple companies and industries is provided; we may modify it but **must disclose our changes**. Full challenge doc: the System One approach must create a meaningful technical unlock with clear customer value ("technical novelty alone is not enough"), demonstrated by our own realistic evaluation against classical ML or LLM-based approaches, not toy examples. Using a System One model is encouraged but not strictly required; prefer a System-One-compatible API. Disclosure must say what was changed and why (no location or format given; we use `docs/DATASET_CHANGES.md`, README and Devpost). Prizes: $200 / $100 / $50 gift card per person. QuickBooks and Sentry are not in Devpost's prize list; ask organizers how to opt in. | VERIFIED | P1 |
| F6 | Dataset location | `https://huggingface.co/datasets/CL251215/QuickBooks-Hackathon-2026` | VERIFIED | P1 |
| F7 | Dataset structure | Files, columns, row counts, number of companies. One 26.7 MB tarball: 100 companies (71 industries), one directory per company with one YAML file per entity type (accounts, vendors, bills, journal entries, invoices, payments, bank feed, reports), 2025-10-06 to 2026-10-06. Raw IDs are company-scoped. Real vendor table (821 vendors). Proposed `ledger.transactions` = bill lines + expense journal lines (159,790 rows, reconciles to each company's P&L to the cent). Details, mapping, sign rule and split in `docs/DATASET.md`. | VERIFIED | P1 (Phase 0) |
| F8 | Jev API | `POST /v1/systemone`; default base URL `https://api.typesafe.ai`. Body: `model`, `state` (string, JSON object, or array of text), `questions` keyed by our IDs. Question types: `noul` (yes/no probability; optional `criteria` with `true`/`false` descriptions), `choice` (`criteria` is a map of option → description or null; 1–255 options), `score` (`criteria` is an ordered list of 1–10 levels). Choice answers return `choice`, `probabilities`, `confidence`; score answers return `score`, `legend`, `probabilities`, `confidence`; noul answers return `noul` only. | VERIFIED | P2 |
| F9 | Jev Python SDK | `from typesafe_sdk import Choice, Noul, Score, TypeSafeClient`; `client.system_one(state=..., questions=...)`; read `response.answers[id].noul / .choice / .score`. Client reads `TYPESAFE_API_KEY` and defaults to `jev-latest`. Async client and retry policy exist (`AsyncTypeSafeClient`, `RetryPolicy`). | VERIFIED | P2 |
| F10 | Jev versions | Jev 1.14 is newest (from Oct 14, 2026); versioned IDs like `jev-1.14.0` are accepted. The `jev-latest` alias can move and shift probabilities, so we pin. Whether the SDK honors a `TYPESAFE_DEFAULT_MODEL` env var vs. needs a constructor argument: | VERIFY | P2 |
| F11 | Jev behavior | Without an "other" option, Jev still forces a pick from the listed options, just with low confidence. Jev cannot be fine-tuned. Rate limits and our access level: | VERIFIED / **UNKNOWN** (limits) | P2 |
| F12 | Sentry agent tracing | Span ops `gen_ai.invoke_agent` (container), `gen_ai.chat` (LLM call), `gen_ai.execute_tool` (tool call), nested under the agent span. Names: `invoke_agent {agent name}`, `execute_tool {tool name}`. Span attributes must be primitives; lists/dicts must be JSON strings. Python records prompts/outputs only with `send_default_pii=True`. Sample AI traces at 100% or whole runs get dropped. Sentry can export agent conversations via its CLI or MCP server. | VERIFIED | P2 |
| F13 | Sentry details | Exact Python method for setting span attributes in our SDK version, how to enable Logs in Python, Session Replay setup in the web SDK, exact CLI/MCP export commands. | VERIFY | P2 |
| F14 | Veri | Python SDK `veri_sdk`. Verified snippets: `Client()`, `client.code_artifacts.upload(dir)`, `client.training_jobs.create_custom_script(entrypoint=..., code_artifact_id=..., base_image=..., gpu_type=..., gpu_count=..., output_name=..., env={...})`, `job.wait(poll_interval=...)`, `job.download(output_dir=...)`, and a `@training_job(base_model=..., dataset="hf:...", gpu_type=..., gpu_count=...)` decorator around a reward function for RL. GPU types seen: `L4-24GB`, `A10G-24GB`, `A100-80GB`, `H100-80GB`. Deployed models get an OpenAI-compatible endpoint. Has a LoRA single-GPU demo and an agent-GRPO demo. | VERIFIED | P3 |
| F15 | Veri unknowns | Answered in `docs/VERI.md` (from docs, SDK source and offline runs; nothing run against the API yet). Key: SDK reads `VERI_API_KEY`; managed SFT is `method="sft_text"` with LoRA, rejects our 9.8 line as is, computes loss on the full conversation for `messages` rows and fixes the seed at 42; one row per assistant turn (`prompt`/`completion`) gives assistant-only loss; a multi-adapter deployment serves base plus LoRA versions by `model` name and needs `--enable-auto-tool-choice --tool-call-parser hermes`. Per account: 4 training jobs, 2 replicas. | VERIFIED (docs/source) / VERIFY (live API) | P3 |
| F16 | vLLM fallback | `vllm serve <base> --enable-lora --lora-modules name=path` serves adapters on an OpenAI-compatible server; request an adapter by passing its name as `model`; `/v1/models` lists adapters. Tool-calling flags (`--enable-auto-tool-choice`, `--tool-call-parser`) exist; the right parser for our base model and runtime adapter loading: | VERIFIED (parser: `hermes` for Qwen3-4B-Instruct-2507; runtime loading: see `docs/VERI.md` section 6) | P3 |
| F17 | GEPA | Available as `dspy.GEPA` and as the standalone `gepa` package (`gepa.optimize(seed_candidate=..., trainset=..., valset=..., max_metric_calls=..., reflection_lm=...)`) with a custom adapter class implementing `evaluate(batch, candidate, capture_traces)` and `make_reflective_dataset(...)`. Exact keyword for passing the adapter: `adapter=` (then `task_lm` and `evaluator` must be None). `gepa` 0.1.4 (latest on PyPI): the adapter must subclass `gepa.GEPAAdapter` (or set `propose_new_texts = None`); the docs example's plain class silently never mutates. `evaluate` returns `EvaluationBatch(outputs, scores, trajectories, objective_scores, num_metric_calls)`; `make_reflective_dataset(candidate, eval_batch, components_to_update)` returns `{component: [{"Inputs", "Generated Outputs", "Feedback"}]}`. A string `reflection_lm` needs litellm (not installed by default), so pass a callable `(str or list[dict]) -> str` wrapping the `openai` client. `max_metric_calls` counts per example, checked between iterations (can overshoot slightly). Result: `result.best_candidate`. `seed=0` covers candidate selection and batch order, not LM sampling. | VERIFIED | P1 |
| F18 | Base model | Qwen3.5-4B exists but Veri's training stack cannot load it, it thinks by default and its template breaks on our tool-call arguments. `Qwen/Qwen3-4B-Instruct-2507` trains and serves on Veri, does not think, and emits Hermes tool calls (`docs/VERI.md` section 3). Baseline accuracy: measured at P1-07. | VERIFIED (docs/source) | P3 |
| F19 | Tev1 fallback for Jev | Together's open Tev1 runs on Ollama behind a `/v1/systemone` endpoint following Jev's API; TypeSafe's SDK can point at it by setting `TYPESAFE_BASE_URL`. Tev1 is weaker than Jev and its probabilities are less calibrated. | VERIFIED | P2 |
| F20 | Prior art | AgentScope + Trinity-RFT (production logs → LLM judge → automatic fine-tuning); GEPA reported to outperform GRPO with up to 35× fewer rollouts; Autopsy at LA Hacks spring 2026. | VERIFIED | all |
| F21 | Our DDL and SQL-tool isolation | The DDL in 8.6–8.7 was applied to a real Postgres 16. With `SET LOCAL ROLE scar_sql_tool` and `app.company_id` set, `sql_query` sees only the current company's rows and is denied `ledger.companies`, `ledger.planted_anomalies`, and the whole `app` schema. The DB user must be allowed to create roles (the Docker image's default user is a superuser, so this works out of the box). | VERIFIED | P3 |

**Rule for this table:** when you verify something, update this table in a PR (Section 8.9 does not apply to this table; just tell the team) and add details to your docs file (`docs/DATASET.md`, `docs/JEV.md`, `docs/SENTRY.md`, `docs/VERI.md`).

---

## 5. Prizes and how we qualify

| Prize | Requirement (summary) | How we qualify | Owner |
|---|---|---|---|
| **Sentry** | Two+ products beyond error monitoring; show observability shaped the build | AI agent monitoring + Tracing on every run, Logs from all services, Session Replay on the web UI. Our training data source is literally agent runs; the demo walks through a Sentry trace. | P2 |
| **Veri** | Post-train/fine-tune a model with Veri | Every arm-D generation is a Veri training job; we show measured gains on held-out companies. | P3 |
| **QuickBooks** | System One models like Jev; use their data; disclose changes | Jev triages every agent run in real time (too slow/costly with LLM judges) and classifies judges' questions into task templates. Accounting agent on their data. All modifications listed in `docs/DATASET_CHANGES.md` and on Devpost. | P1 (data), P2 (Jev) |
| **ML track** | Systems that learn, adapt, and improve from data | The whole project. | all |

**Optional low-effort MLH stacks** (check MLH rules first): host Postgres on Tiger Data (Best Use of Tiger Data), serve vLLM on a Vultr GPU if we use the fallback (Best Use of Vultr), register a domain for the web UI (GoDaddy Registry). Do not let these distract from the core.

---

## 6. System architecture

### 6.1 Components

| Component | Owner | Process / port | Responsibility |
|---|---|---|---|
| Postgres 16 | P3 sets up (docker compose); tables owned per Section 8.6 | `localhost:5432` | All state: ledger and app tables |
| **env service** (`scar_env`) | P1 | FastAPI `:8001` | Ledger loading, splits, templates, task generation, verifier, tool execution, mutator, traffic simulator, learn-steps for arms B and C |
| **agent service** (`scar_agent`) | P2 | FastAPI `:8002` | Agent loop, Sentry instrumentation, run storage, failure miner (Jev), judge-question parsing |
| **train service** (`scar_train`) | P3 | FastAPI `:8003` | Model registry, training-data builder, Veri jobs, promotion gate, loop runner for all arms |
| **web** | P2 | Next.js `:3000` | Judge UI, dashboard, run viewer, Session Replay |
| Model endpoint | P3 | Veri deployment (preferred) or vLLM `:8000` (fallback) | OpenAI-compatible chat completions for base and fine-tuned models |
| Jev | external (P2 integrates) | `https://api.typesafe.ai` | Failure triage, judge-question template classification |
| Sentry | external (P2 integrates) | sentry.io | Tracing, AI agent monitoring, Logs, Session Replay |

### 6.2 Data flow

```
                         ┌──────────────── web :3000 (P2) ───────────────┐
                         │ judge UI · dashboard · run viewer · replay     │
                         └───────┬───────────────────────────┬────────────┘
                                 │ /judge/*, /runs           │ /generations, /models
                                 ▼                           ▼
┌─ env :8001 (P1) ─┐   runs   ┌─ agent :8002 (P2) ─┐     ┌─ train :8003 (P3) ─────────┐
│ templates/tasks  │◄────────►│ agent loop          │◄────│ loop runner (all arms)      │
│ /tools/execute   │  tools   │ Sentry spans        │ run │ data builder · gate         │
│ /verify          │  verify  │ /failures/mine ─► Jev│     │ Veri jobs · registry        │
│ /mutate /simulate│─────────►│                     │     └──────────┬─────────────────┘
│ /learn/B /learn/C│ traffic  └─────────┬───────────┘                │ train / deploy
└────────┬─────────┘                    │ chat.completions           ▼
         │                              └──────────────────► model endpoint (Veri or vLLM)
         ▼
   Postgres: ledger.* (P1) · app.* (owners in 8.6)
```

### 6.3 Lifecycle of one generation (arm D, seed s, generation g)

The loop runner in the train service (P3) drives every step. Each step's result is stored, so a crashed generation can resume from its last completed step.

1. **collect** — train calls env `POST /simulate` with the current promoted model and prompt. env samples `TRAFFIC_PER_GEN` train-split tasks with a schedule seed that is identical across arms, and calls agent `POST /agent/run_batch` with `purpose="traffic"`. The agent runs each task, calls env `/tools/execute` for tools and env `/verify` at the end, stores the `RunResult`, and emits Sentry spans.
2. **mine** — train calls agent `POST /failures/mine` for this arm/seed/generation. The agent sends each failed traffic or judge run to Jev and stores a `FailureRecord`, applying the inclusion rule and the per-generation cap.
3. **mutate** — train calls env `POST /mutate` with the included failure IDs. env stores and returns variant tasks (train split only).
4. **build_data** — train calls agent `POST /agent/run_batch` with `purpose="sampling"`, `n=K_SAMPLES`, `temperature=SAMPLE_TEMPERATURE` on the variants and the original failed tasks. It keeps passed attempts, adds replay examples, and writes `TrainingExample` rows under a snapshot ID.
5. **train** — train launches a Veri job on the cumulative snapshot for this arm/seed lineage, waits, and registers a candidate `ModelVersion`.
6. **deploy** — train makes the candidate callable at an OpenAI-compatible endpoint and records `endpoint_base_url` and `endpoint_model_id`.
7. **gate** — train runs the frozen eval set and the regression set on the candidate (`purpose="eval"` and `"regression"`), computes scores, and applies the promotion rule.
8. **promote or reject** — train marks the candidate promoted (it becomes current for the next generation) or rejected (the parent stays current).
9. **record** — train finalizes the `GenerationRecord`. The dashboard reads it.

Arms B and C follow the same steps, except steps 4–6 are replaced by a call to env `POST /learn/B` or `/learn/C`, which returns a new candidate prompt version. Arm A skips learning entirely. Arm E does D's steps, then C's learn-step on the new weights.

---

## 7. Tech stack, repo layout, and conventions

### 7.1 Stack (decided)

| Layer | Choice |
|---|---|
| Language | Python 3.11+ for all services; TypeScript for web |
| Python packaging | `uv`, **one project per service** (`schemas/`, `env/`, `agent/`, `train/`, `experiments/`), each with its own `pyproject.toml` and lockfile. Services depend on `scar_schemas` through a local path dependency. This prevents lockfile merge conflicts. |
| Web framework | FastAPI + Uvicorn for services; Next.js (App Router) + TypeScript for web |
| Models / validation | Pydantic v2 |
| Database | Postgres 16 via `docker compose`; `psycopg` (v3) with plain SQL. No ORM. |
| HTTP client | `httpx` (async where concurrency matters) |
| LLM client | Official `openai` Python package pointed at OpenAI-compatible endpoints via `base_url` |
| Jev | `typesafe_sdk` |
| Observability | `sentry-sdk` (Python), `@sentry/nextjs` (web) |
| Prompt optimization | `gepa` package (arm C) |
| Tests | `pytest`; lint/format with `ruff` |

### 7.2 Repo layout and ownership

```
scar-tissue/
├── docs/
│   ├── SPEC.md                  # this file — all three (change protocol 8.9)
│   ├── DECISIONS.md             # append-only, one heading per person
│   ├── DATASET.md               # P1: real dataset structure
│   ├── DATASET_CHANGES.md       # P1: every modification we make (QuickBooks disclosure)
│   ├── TEMPLATES.md             # P1: template catalog with answer definitions
│   ├── JEV.md                   # P2: verified Jev usage notes
│   ├── SENTRY.md                # P2: verified Sentry setup notes
│   ├── VERI.md                  # P3: verified Veri usage notes
│   ├── RESULTS.md               # P1: final experiment table and plots
│   └── DEMO.md                  # P2 owns (script, question list); P3 adds the demo-generation procedure under its own heading
├── schemas/                     # SHARED — package scar_schemas (P3 creates it in Phase 0, then frozen)
│   ├── pyproject.toml
│   └── scar_schemas/
│       ├── __init__.py
│       ├── constants.py         # 8.1
│       ├── models.py            # 8.2
│       ├── openai_format.py     # 8.3
│       ├── ids.py               # 7.4
│       ├── tools_v1.json        # 8.4
│       └── ddl.sql              # 8.6, 8.7
├── fixtures/
│   ├── tasks_seed.jsonl         # P1
│   ├── runs_sample.jsonl        # P2
│   ├── failures_sample.jsonl    # P2
│   └── training_examples_sample.jsonl  # P3
├── env/                         # P1 — package scar_env (env service)
├── experiments/                 # P1 — package scar_experiments (arms B and C learn-steps)
├── agent/                       # P2 — package scar_agent (agent service)
│   └── scar_agent/prompts/system_v1.md   # P2 (Section 9.2)
├── web/                         # P2 — Next.js app
├── train/                       # P3 — package scar_train (train service)
├── scripts/                     # P3 — smoke tests for each integration checkpoint
├── docker-compose.yml           # P3
├── .env.example                 # P3 creates; each person adds their own vars by PR
├── .github/CODEOWNERS           # P3
└── README.md                    # P1 (Devpost-facing)
```

`CODEOWNERS` (replace handles):

```
/schemas/      @person1 @person2 @person3
/docs/SPEC.md  @person1 @person2 @person3
/env/          @person1
/experiments/  @person1
/README.md     @person1
/agent/        @person2
/web/          @person2
/train/        @person3
/scripts/      @person3
/docker-compose.yml @person3
```

### 7.3 Git workflow

`main` is always runnable. Each person works on short-lived branches named `p1/<topic>`, `p2/<topic>`, `p3/<topic>` and merges by pull request. A PR that touches only your own paths can be merged by you after tests pass. A PR that touches `schemas/` or `docs/SPEC.md` needs approval from at least one other person and a message to all three (Section 8.9). Rebase on `main` before merging. Never force-push `main`.

### 7.4 Conventions

**IDs.** Strings of the form `<prefix>_<uuid4 hex>`, created only with `scar_schemas.ids.new_id(prefix)`. Prefixes: `task`, `run`, `fail`, `ex`, `snap`, `job`. Exceptions with readable formats: model versions (`base`, `{arm}-s{seed}-g{generation}`), generation IDs (same format as model versions), prompt versions (`system_v1`, `B-s{seed}-g{generation}`, `C-s{seed}-g{generation}`, `C-cand-<hex>` for GEPA candidates), eval set (`eval_v1`), tools version (`tools_v1`).

```python
# scar_schemas/ids.py
import uuid

def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"
```

**Time.** All datetimes are timezone-aware UTC (`datetime.now(timezone.utc)`), serialized ISO 8601. Ledger dates are `YYYY-MM-DD` strings; months are `YYYY-MM`.

**Money.** `Decimal` in Python, `NUMERIC(14,2)` in Postgres, strings in JSON (Pydantic v2's default for Decimal). Never use floats for money in our own code. Values the model submits as JSON numbers are converted with `Decimal(str(x))`.

**Amount sign.** In the canonical ledger, `amount` is positive for money spent and negative for refunds/credits. P1 documents the exact mapping from the raw dataset in `docs/DATASET.md`.

**Logging.** JSON lines to stdout with keys `ts`, `level`, `service`, `msg`, plus any of `run_id`, `task_id`, `arm`, `seed`, `generation`, `model_version` that apply. Never log secrets.

**HTTP errors.** Services return `422` for invalid input (FastAPI default), `404` for unknown IDs, `409` for state conflicts (e.g. a generation step already done), `503` when a dependency (model endpoint, Jev, Veri) is unavailable. Error body: `{"error": "<short_code>", "detail": "<human-readable>"}`.

**Timeouts.** Model call 60 s per turn. Tool call 10 s. Whole run 180 s. Jev call 30 s with up to 3 retries. Verify 10 s.

### 7.5 Environment variables

One `.env` at the repo root, loaded by every service. `.env.example` lists every variable with no real values.

```bash
# --- shared ---
DATABASE_URL=postgresql://scar:scar@localhost:5432/scar
ENV_SERVICE_URL=http://localhost:8001
AGENT_SERVICE_URL=http://localhost:8002
TRAIN_SERVICE_URL=http://localhost:8003

# --- Person 1 ---
HF_TOKEN=                         # only if the dataset requires it
REFLECTION_LM_BASE_URL=           # OpenAI-compatible endpoint of a strong model (lessons + GEPA)
REFLECTION_LM_API_KEY=
REFLECTION_LM_MODEL=
PARAPHRASE_LM_BASE_URL=           # optional; question paraphrasing for variants
PARAPHRASE_LM_API_KEY=
PARAPHRASE_LM_MODEL=

# --- Person 2 ---
SENTRY_DSN=                       # backend Sentry project
NEXT_PUBLIC_SENTRY_DSN=           # web Sentry project
SENTRY_ENVIRONMENT=hackathon
TYPESAFE_API_KEY=
TYPESAFE_DEFAULT_MODEL=jev-1.14.0 # VERIFY the SDK honors this; otherwise pass the model explicitly
TYPESAFE_BASE_URL=                # leave empty for Jev; set only for the Tev1 fallback (F19)
PARSE_LM_BASE_URL=                # OpenAI-compatible model for judge-question parameter extraction
PARSE_LM_API_KEY=
PARSE_LM_MODEL=
SENTRY_ORG=                       # for building trace links in the web UI (URL format: VERIFY)
SENTRY_PROJECT=
DEV_MODEL_BASE_URL=               # DEV ONLY: used when model_version == "dev" (before P3 registers "base")
DEV_MODEL_ID=
DEV_MODEL_API_KEY=

# --- Person 3 ---
VERI_API_KEY=                     # VERIFY the exact variable name Veri expects
BASE_MODEL_HF_ID=                 # decided in Phase 0 (D-04)
MODEL_API_KEY=                    # key for the model endpoint(s) in the registry
TEACHER_BASE_URL=                 # stretch: teacher path
TEACHER_API_KEY=
TEACHER_MODEL=
```

The agent never reads a model URL from `.env` directly. It looks up `endpoint_base_url` and `endpoint_model_id` for the requested `model_version` in `app.model_versions` (Section 8.6) and authenticates with `MODEL_API_KEY`. The single exception is `model_version == "dev"`, which uses `DEV_MODEL_*` so Person 2 can build the agent before Person 3 registers the real `base` model. Runs made with `dev` are for development only and are never used for training, eval, or results.

---

## 8. Shared contracts

Everything in this section lives in the `scar_schemas` package. Person 3 creates the package in Phase 0 by copying these blocks verbatim, and the team reviews it once. After IC-0 it is frozen (Section 8.9).

### 8.1 Constants

```python
# scar_schemas/constants.py — SHARED CONTRACT
SCHEMA_VERSION = "1.2.0"
TOOLS_VERSION = "tools_v1"
BASE_PROMPT_VERSION = "system_v1"
EVAL_SET_ID = "eval_v1"

# splits and pools
SPLIT_SEED = 7                 # company → split assignment
EVAL_COMPANY_FRACTION = 0.30   # fraction of companies in eval split (min 2 eval companies)
TRAIN_POOL_SIZE = 2000         # pre-generated train traffic tasks
TRAIN_POOL_SEED = 42
EVAL_SET_SIZE = 300            # 100 per family
EVAL_SET_SEED = 1234

# agent
MAX_STEPS = 12
MAX_TOKENS_PER_TURN = 1024
TOOL_RESULT_MAX_CHARS = 6000
EVAL_TEMPERATURE = 0.0

# loop
SEEDS = (0, 1, 2)
N_GENERATIONS = 4              # generations 1..4 after generation 0
TRAFFIC_PER_GEN = 200
FAILURE_CAP_PER_GEN = 25       # max included failures used per generation (was 50; 1.1.0)
INCLUDE_LEARNABLE_P = 0.6
VARIANTS_PER_FAILURE = 5       # was 10 (1.1.0, SPEC 9.13 fallback)
K_SAMPLES = 4                  # was 8 (1.1.0, SPEC 9.13 fallback)
SAMPLE_TEMPERATURE = 0.8
MAX_EXAMPLES_PER_TASK = 2
REPLAY_RATIO = 1.0             # replay examples per self example
REGRESSION_SET_SIZE = 100
REGRESSION_MIN_PASS = 0.95

# arm B (memory) and arm C (GEPA)
MEMORY_MAX_LESSONS = 20
GEPA_MAX_METRIC_CALLS = 150
GEPA_TRAIN_EXTRA = 50          # random train tasks added to GEPA trainset
GEPA_VAL_SIZE = 50
```

**1.1.0 (2026-10-18):** `K_SAMPLES` 8 to 4 and `VARIANTS_PER_FAILURE` 10 to 5 (the first fallback in 9.13) and `FAILURE_CAP_PER_GEN` 50 to 25, because one Veri L4 replica serves every agent run and a full-size generation (about 5,000 runs) would take several hours each; traffic, eval and regression sizes are unchanged.

**1.2.0 (2026-10-18):** `to_openai_messages` emits `{"__raw__": s}` tool-call arguments as that JSON object instead of the bare string `s`. vLLM parses every earlier tool call's arguments with `json.loads` before applying the chat template, so a transcript in which the model once wrote malformed arguments made every later request fail with HTTP 400 and the run end as `model_error` (reproduced on `base`: the raw form returns 400, the wrapped form returns 200 and the model retries with a valid call). The training export's `json.loads` of the same strings now succeeds too, and `from_openai_message` parses the wrapped form back to `{"__raw__": s}`, so round trips are unchanged.

These are starting values. If a value must change (for example, `K_SAMPLES` down to 4 because sampling is too slow), follow Section 8.9 so all three services change together.

### 8.2 Models

```python
# scar_schemas/models.py — SHARED CONTRACT, schema version 1.0.0
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

Arm = Literal["A", "B", "C", "D", "E"]
Purpose = Literal["traffic", "sampling", "eval", "regression", "judge", "gepa", "smoke"]
StopReason = Literal["submitted", "max_steps", "no_submit", "model_error", "tool_error", "timeout"]


class Split(str, Enum):
    train = "train"
    eval = "eval"


class TaskFamily(str, Enum):
    aggregation = "aggregation"
    anomaly = "anomaly"
    bookkeeping = "bookkeeping"


class Origin(str, Enum):
    seed = "seed"            # hand-written fixtures
    generated = "generated"  # template generator (train pool)
    variant = "variant"      # mutated from an included failure
    judge = "judge"          # created from the judge UI
    eval = "eval"            # frozen eval set


class AnswerType(str, Enum):
    number = "number"                # one Decimal, compared with tolerance
    id_set = "id_set"                # set of ledger IDs, exact set match
    journal_entry = "journal_entry"  # sandbox entries written during the run


# ---------- tasks ----------

class JournalLine(BaseModel):
    account_id: str
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    memo: str | None = None


class JournalEntrySpec(BaseModel):
    entry_date: str              # YYYY-MM-DD
    lines: list[JournalLine]


class ExpectedAnswer(BaseModel):
    answer_type: AnswerType
    number: Decimal | None = None
    tolerance: Decimal = Decimal("0.01")
    id_set: list[str] | None = None
    journal_entries: list[JournalEntrySpec] | None = None


class Task(BaseModel):
    task_id: str
    company_id: str
    split: Split
    family: TaskFamily
    template_id: str
    params: dict[str, Any]
    prompt: str                  # shown to the agent
    expected: ExpectedAnswer     # NEVER shown to the agent or sent to any LLM
    difficulty: int = Field(ge=1, le=3)
    origin: Origin
    parent_failure_id: str | None = None
    created_at: datetime


class TemplateSpec(BaseModel):
    template_id: str             # e.g. "agg.total_by_account_period"
    family: TaskFamily
    answer_type: AnswerType
    description: str             # one sentence; also used as a Jev Choice criterion
    params_schema: dict[str, Any]  # JSON Schema for params
    phrasings: list[str]         # >= 5 question templates with {param} placeholders
    difficulty: int = Field(ge=1, le=3)


# ---------- transcripts ----------

class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]    # parsed JSON; unparseable args stored as {"__raw__": "<string>"}


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[ToolCall] | None = None   # assistant only
    tool_call_id: str | None = None            # tool only
    name: str | None = None                    # tool only: tool name


# ---------- runs ----------

class RunRequest(BaseModel):
    task_id: str
    model_version: str                     # key into app.model_versions
    prompt_version: str = "system_v1"      # key: "system_v1" (file) or app.prompt_versions
    arm: Arm = "D"
    seed: int = 0
    generation: int = 0
    purpose: Purpose
    temperature: float = 0.0
    n: int = Field(default=1, ge=1, le=16) # independent attempts
    max_steps: int = 12


class RunBatchRequest(BaseModel):
    requests: list[RunRequest]
    max_concurrency: int = Field(default=8, ge=1, le=64)


class RunResult(BaseModel):
    run_id: str
    task_id: str
    company_id: str
    split: Split
    family: TaskFamily
    model_version: str
    endpoint_model_id: str
    prompt_version: str
    tools_version: str
    arm: Arm
    seed: int
    generation: int
    purpose: Purpose
    origin: Origin
    attempt_index: int
    temperature: float
    messages: list[ChatMessage]            # exactly as sent to / received from the model
    final_answer: dict[str, Any] | None    # arguments of submit_answer, or None
    stop_reason: StopReason
    passed: bool
    verify_detail: str
    steps: int
    input_tokens: int
    output_tokens: int
    latency_ms: int
    sentry_trace_id: str | None
    created_at: datetime


# ---------- env: tools and verification ----------

class ToolExecuteRequest(BaseModel):
    run_id: str
    company_id: str
    name: str
    arguments: dict[str, Any]


class ToolExecuteResult(BaseModel):
    ok: bool
    result: Any | None = None    # JSON-serializable
    error: str | None = None     # message for the model when ok is False


class VerifyRequest(BaseModel):
    task_id: str
    run_id: str
    stop_reason: StopReason
    final_answer: dict[str, Any] | None


class VerifyResult(BaseModel):
    passed: bool
    detail: str                  # e.g. "number: expected 1234.50, got 1243.50"


# ---------- failures ----------

class FailureCause(str, Enum):
    wrong_tool = "wrong_tool"
    bad_tool_args = "bad_tool_args"
    reasoning_error = "reasoning_error"
    wrong_final_format = "wrong_final_format"
    gave_up = "gave_up"
    environment_error = "environment_error"
    ambiguous_task = "ambiguous_task"
    other = "other"


class FailureRecord(BaseModel):
    failure_id: str
    run_id: str
    task_id: str
    arm: Arm
    seed: int
    generation: int
    model_version: str
    origin: Origin
    cause: FailureCause
    cause_confidence: float
    cause_probabilities: dict[str, float]
    learnable_p: float
    included: bool
    exclusion_reason: str | None
    jev_model: str
    created_at: datetime


class MineRequest(BaseModel):
    arm: Arm
    seed: int
    generation: int
    model_version: str
    prompt_version: str


# ---------- env: generation, mutation, traffic ----------

class GenerateRequest(BaseModel):
    split: Split
    n: int
    seed: int
    origin: Origin               # generated or eval
    family: TaskFamily | None = None


class FromParamsRequest(BaseModel):
    template_id: str
    company_id: str
    params: dict[str, Any]
    origin: Origin               # judge
    prompt: str | None = None    # judge's own wording; if None a phrasing is used
    dry_run: bool = False        # True: validate and describe, do not store


class FromParamsResult(BaseModel):
    task: Task | None            # None when dry_run is True
    interpretation: str          # canonical one-sentence restatement, no answer included
    valid: bool
    error: str | None = None


class MutateRequest(BaseModel):
    failure_ids: list[str]
    variants_per_failure: int = 10
    seed: int


class SimulateRequest(BaseModel):
    arm: Arm
    seed: int
    generation: int
    model_version: str
    prompt_version: str
    n_tasks: int = 200


class SimulateResult(BaseModel):
    run_ids: list[str]
    passed: int
    failed: int


class EvalSet(BaseModel):
    eval_set_id: str
    task_ids: list[str]          # sorted
    sha256: str                  # sha256 of "\n".join(sorted task_ids)


# ---------- prompts and learn-steps (arms B, C) ----------

class PromptVersion(BaseModel):
    prompt_version: str
    arm: Arm
    seed: int
    generation: int
    text: str
    parent_prompt_version: str | None
    is_candidate: bool = False   # True for GEPA intermediate candidates
    created_at: datetime


class LearnRequest(BaseModel):
    arm: Literal["B", "C"]
    seed: int
    generation: int
    model_version: str
    current_prompt_version: str
    failure_ids: list[str]       # included failures for this generation


class LearnResult(BaseModel):
    prompt_version: str          # new candidate prompt (stored in app.prompt_versions)
    notes: str


# ---------- training and registry ----------

class TrainingExample(BaseModel):
    example_id: str
    snapshot_id: str
    lineage: str                 # f"{arm}-s{seed}", e.g. "D-s0"
    task_id: str
    source: Literal["self", "teacher", "replay"]
    origin_run_id: str
    prompt_version: str
    tools_version: str
    messages: list[ChatMessage]  # verbatim from the passed RunResult
    created_at: datetime


class ModelVersion(BaseModel):
    version: str                 # "base" or f"{arm}-s{seed}-g{generation}"
    arm: Arm | None              # None for base
    seed: int | None
    generation: int
    parent_version: str | None
    base_model_hf_id: str
    endpoint_base_url: str
    endpoint_model_id: str       # value sent as `model` in chat.completions
    adapter_uri: str | None
    training_job_id: str | None
    snapshot_id: str | None
    status: Literal["candidate", "promoted", "rejected", "failed"]
    created_at: datetime


class GenStatus(str, Enum):
    collecting = "collecting"
    mining = "mining"
    mutating = "mutating"
    building_data = "building_data"
    training = "training"
    deploying = "deploying"
    learning_prompt = "learning_prompt"   # arms B, C, E
    gating = "gating"
    promoted = "promoted"
    rejected = "rejected"
    failed = "failed"


class GenerationRecord(BaseModel):
    generation_id: str           # f"{arm}-s{seed}-g{generation}"
    arm: Arm
    seed: int
    generation: int
    status: GenStatus
    parent_model_version: str
    parent_prompt_version: str
    candidate_model_version: str | None
    candidate_prompt_version: str | None
    current_model_version: str   # in use after this generation finishes
    current_prompt_version: str
    traffic_runs: int = 0
    failures_mined: int = 0
    failures_included: int = 0
    variants_created: int = 0
    examples_built: int = 0
    eval_score: float | None = None        # candidate's score on eval_v1
    parent_eval_score: float | None = None
    regression_score: float | None = None
    notes: str = ""
    started_at: datetime
    finished_at: datetime | None = None


# ---------- judge UI ----------

class JudgeParseRequest(BaseModel):
    company_id: str              # must be a train-split company
    question: str


class JudgeParseResult(BaseModel):
    template_id: str | None
    template_confidence: float
    params: dict[str, Any]
    interpretation: str          # shown to the judge to confirm
    valid: bool
    error: str | None = None


class JudgeRunRequest(BaseModel):
    company_id: str
    template_id: str
    params: dict[str, Any]
    question: str


class JudgeRunResult(BaseModel):
    task_id: str
    run: RunResult
    sentry_trace_url: str | None
```

### 8.3 OpenAI message format conversion

The agent and the training-data builder must use these functions, so a transcript is serialized identically at inference and in training (R7).

```python
# scar_schemas/openai_format.py — SHARED CONTRACT (signatures and behavior)
import json
from importlib import resources

from .models import ChatMessage, ToolCall

def dumps_args(args: dict) -> str:
    """Deterministic JSON for tool arguments: sort_keys=True, separators=(",", ":"), ensure_ascii=False."""
    return json.dumps(args, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

def to_openai_messages(messages: list[ChatMessage]) -> list[dict]:
    """Convert to OpenAI chat format.
    assistant tool calls -> {"id": c.id, "type": "function",
                             "function": {"name": c.name, "arguments": dumps_args(c.arguments)}}
    tool messages        -> {"role": "tool", "tool_call_id": ..., "name": ..., "content": ...}
    Omit keys whose value is None. Arguments are always valid JSON: {"__raw__": s} is emitted as the JSON
    object itself (dumps_args), never as the bare string s (1.2.0)."""

def from_openai_message(msg: dict) -> ChatMessage:
    """Inverse of the above for one message returned by the model.
    Parse function.arguments with json.loads; on failure store {"__raw__": original_string}."""

def load_tools(version: str = "tools_v1") -> list[dict]:
    """Return the tool list from scar_schemas/tools_v1.json (OpenAI `tools` format)."""
```

### 8.4 Tool definitions (`tools_v1.json`)

The agent sends exactly this list as `tools` on every model call. Tools never take `company_id`; the env service applies the task's company automatically, so the agent cannot read other companies (including eval companies).

```json
[
  {"type": "function", "function": {
    "name": "get_accounts",
    "description": "List the chart of accounts for the current company: account_id, name, account_type.",
    "parameters": {"type": "object", "properties": {}, "required": []}}},
  {"type": "function", "function": {
    "name": "get_vendors",
    "description": "List vendors for the current company, optionally filtered by a case-insensitive name substring.",
    "parameters": {"type": "object", "properties": {
      "name_contains": {"type": "string"}}, "required": []}}},
  {"type": "function", "function": {
    "name": "get_transactions",
    "description": "List transactions for the current company, ordered by txn_date then txn_id. Dates are inclusive, format YYYY-MM-DD. Returns at most `limit` rows (default 100).",
    "parameters": {"type": "object", "properties": {
      "start_date": {"type": "string"},
      "end_date": {"type": "string"},
      "account_id": {"type": "string"},
      "vendor_id": {"type": "string"},
      "min_amount": {"type": "number"},
      "max_amount": {"type": "number"},
      "limit": {"type": "integer", "minimum": 1, "maximum": 500}}, "required": []}}},
  {"type": "function", "function": {
    "name": "sql_query",
    "description": "Run one read-only SQL SELECT over ledger.accounts, ledger.vendors and ledger.transactions. Rows are automatically restricted to the current company. Returns at most 200 rows.",
    "parameters": {"type": "object", "properties": {
      "sql": {"type": "string"}}, "required": ["sql"]}}},
  {"type": "function", "function": {
    "name": "create_journal_entry",
    "description": "Record one journal entry for the current company in a sandbox. Total debits must equal total credits. Returns entry_id.",
    "parameters": {"type": "object", "properties": {
      "entry_date": {"type": "string"},
      "lines": {"type": "array", "items": {"type": "object", "properties": {
        "account_id": {"type": "string"},
        "debit": {"type": "number"},
        "credit": {"type": "number"},
        "memo": {"type": "string"}}, "required": ["account_id"]}}},
      "required": ["entry_date", "lines"]}}},
  {"type": "function", "function": {
    "name": "submit_answer",
    "description": "Submit the final answer and end the task. Use `number` for numeric questions, `ids` for questions asking which transactions or vendors, and `entry_ids` after recording journal entries. Call exactly once.",
    "parameters": {"type": "object", "properties": {
      "number": {"type": "number"},
      "ids": {"type": "array", "items": {"type": "string"}},
      "entry_ids": {"type": "array", "items": {"type": "string"}}}, "required": []}}}
]
```

`submit_answer` is handled by the agent itself (it ends the loop); the other five are executed by env `POST /tools/execute`.

### 8.5 Service APIs

All request and response bodies are the models in 8.2. Base URLs come from `ENV_SERVICE_URL`, `AGENT_SERVICE_URL`, `TRAIN_SERVICE_URL`.

**env service (Person 1, `:8001`)**

| Method + path | Request | Response | Notes |
|---|---|---|---|
| `GET /health` | — | `{"ok": true}` | |
| `GET /companies?split=` | — | `[{"company_id","name","industry","split"}]` | |
| `GET /templates` | — | `list[TemplateSpec]` | |
| `POST /tasks/generate` | `GenerateRequest` | `list[Task]` | Stores tasks. Used once for the train pool and once for `eval_v1`. |
| `POST /tasks/from_params` | `FromParamsRequest` | `FromParamsResult` | Judge tasks; rejects eval-split companies. |
| `GET /tasks/{task_id}` | — | `Task` | The agent must not send `expected` to the model. |
| `GET /eval_sets/{eval_set_id}` | — | `EvalSet` | |
| `POST /tools/execute` | `ToolExecuteRequest` | `ToolExecuteResult` | Bad arguments → `ok=false` with an error message for the model (HTTP 200). Infrastructure failure → HTTP 503. |
| `POST /verify` | `VerifyRequest` | `VerifyResult` | |
| `POST /mutate` | `MutateRequest` | `list[Task]` | Stores variants. Train split only. |
| `POST /simulate` | `SimulateRequest` | `SimulateResult` | Samples from the train pool and calls agent `/agent/run_batch` with `purpose="traffic"`. |
| `POST /learn/B` | `LearnRequest` | `LearnResult` | Memory lessons (Section 9.10). |
| `POST /learn/C` | `LearnRequest` | `LearnResult` | GEPA (Section 9.10). |

**agent service (Person 2, `:8002`)**

| Method + path | Request | Response | Notes |
|---|---|---|---|
| `GET /health` | — | `{"ok": true}` | |
| `POST /agent/run` | `RunRequest` | `list[RunResult]` | `n` attempts. Stores runs. |
| `POST /agent/run_batch` | `RunBatchRequest` | `list[RunResult]` | Flattened, in request order then attempt order. |
| `GET /runs/{run_id}` | — | `RunResult` | |
| `GET /runs?task_id=&model_version=&purpose=&passed=&limit=` | — | `list[RunResult]` | Ordered by `created_at`. |
| `POST /failures/mine` | `MineRequest` | `list[FailureRecord]` | Idempotent per (arm, seed, generation): re-calling returns existing records. |
| `GET /failures?arm=&seed=&generation=&included=` | — | `list[FailureRecord]` | |
| `POST /judge/parse` | `JudgeParseRequest` | `JudgeParseResult` | Jev Choice over templates + LLM parameter extraction + env dry run. |
| `POST /judge/run` | `JudgeRunRequest` | `JudgeRunResult` | Creates the task via env, runs it on the current promoted arm-D seed-0 model. |

**train service (Person 3, `:8003`)**

| Method + path | Request | Response | Notes |
|---|---|---|---|
| `GET /health` | — | `{"ok": true}` | |
| `GET /models` | — | `list[ModelVersion]` | |
| `GET /models/current?arm=&seed=` | — | `{"model_version": str, "prompt_version": str}` | What the next generation (and the judge UI) should use. |
| `GET /generations?arm=&seed=` | — | `list[GenerationRecord]` | Dashboard source. |
| `GET /generations/{generation_id}` | — | `GenerationRecord` | |
| `POST /loop/run` | `{"arm": Arm, "seed": int, "generations": [int]}` | `{"job_id": str}` | Runs asynchronously; resumes incomplete steps. |
| `GET /loop/status/{job_id}` | — | `{"status": str, "current_generation_id": str \| null, "log_tail": [str]}` | |

### 8.6 Database: app tables and write ownership

Pattern: each table has a few indexed columns for querying plus `body JSONB` holding the full Pydantic model (`model.model_dump(mode="json")`). The indexed columns must always equal the corresponding values in `body`. **Only the owner writes; anyone reads.**

```sql
-- scar_schemas/ddl.sql (part 1 of 2) — SHARED CONTRACT
CREATE SCHEMA IF NOT EXISTS ledger;
CREATE SCHEMA IF NOT EXISTS app;

-- owner: Person 1
CREATE TABLE app.tasks (
  task_id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL,
  split TEXT NOT NULL CHECK (split IN ('train','eval')),
  family TEXT NOT NULL,
  template_id TEXT NOT NULL,
  difficulty INT NOT NULL,
  origin TEXT NOT NULL,
  parent_failure_id TEXT,
  body JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX ON app.tasks (split, family, origin);

-- owner: Person 1
CREATE TABLE app.train_pool (
  position INT PRIMARY KEY,                      -- 0..TRAIN_POOL_SIZE-1
  task_id TEXT NOT NULL REFERENCES app.tasks(task_id)
);

-- owner: Person 1
CREATE TABLE app.eval_sets (
  eval_set_id TEXT PRIMARY KEY,
  body JSONB NOT NULL                            -- EvalSet
);

-- owner: Person 1 (written by /tools/execute)
CREATE TABLE app.sandbox_entries (
  entry_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  company_id TEXT NOT NULL,
  entry_date DATE NOT NULL,
  lines JSONB NOT NULL,                          -- list[JournalLine]
  created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX ON app.sandbox_entries (run_id);

-- owner: Person 1 (arms B and C; system_v1 itself lives in a file owned by Person 2)
CREATE TABLE app.prompt_versions (
  prompt_version TEXT PRIMARY KEY,
  body JSONB NOT NULL,                           -- PromptVersion
  created_at TIMESTAMPTZ NOT NULL
);

-- owner: Person 1 (arm B)
CREATE TABLE app.lessons (
  lesson_id TEXT PRIMARY KEY,
  lineage TEXT NOT NULL,                         -- e.g. "B-s0"
  generation INT NOT NULL,
  failure_id TEXT NOT NULL,
  text TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL
);

-- owner: Person 2
CREATE TABLE app.runs (
  run_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  model_version TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  arm TEXT NOT NULL,
  seed INT NOT NULL,
  generation INT NOT NULL,
  purpose TEXT NOT NULL,
  origin TEXT NOT NULL,
  split TEXT NOT NULL,
  family TEXT NOT NULL,
  passed BOOLEAN NOT NULL,
  stop_reason TEXT NOT NULL,
  body JSONB NOT NULL,                           -- RunResult
  created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX ON app.runs (arm, seed, generation, purpose, passed);
CREATE INDEX ON app.runs (task_id, model_version, prompt_version);

-- owner: Person 2
CREATE TABLE app.failures (
  failure_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL UNIQUE,
  arm TEXT NOT NULL,
  seed INT NOT NULL,
  generation INT NOT NULL,
  included BOOLEAN NOT NULL,
  body JSONB NOT NULL,                           -- FailureRecord
  created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX ON app.failures (arm, seed, generation, included);

-- owner: Person 3
CREATE TABLE app.training_examples (
  example_id TEXT PRIMARY KEY,
  snapshot_id TEXT NOT NULL,
  lineage TEXT NOT NULL,
  task_id TEXT NOT NULL,
  source TEXT NOT NULL,
  body JSONB NOT NULL,                           -- TrainingExample
  created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX ON app.training_examples (lineage, snapshot_id);

-- owner: Person 3
CREATE TABLE app.model_versions (
  version TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  body JSONB NOT NULL,                           -- ModelVersion
  created_at TIMESTAMPTZ NOT NULL
);

-- owner: Person 3
CREATE TABLE app.generations (
  generation_id TEXT PRIMARY KEY,
  arm TEXT NOT NULL,
  seed INT NOT NULL,
  generation INT NOT NULL,
  status TEXT NOT NULL,
  body JSONB NOT NULL,                           -- GenerationRecord
  updated_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX ON app.generations (arm, seed, generation);
```

| Table | Writer | Main readers |
|---|---|---|
| `ledger.*` | P1 | env (tools, generator, verifier) |
| `app.tasks`, `app.train_pool`, `app.eval_sets` | P1 | agent, train |
| `app.sandbox_entries` | P1 | env verifier |
| `app.prompt_versions`, `app.lessons` | P1 | agent (loads prompts), train |
| `app.runs`, `app.failures` | P2 | env (mutator, GEPA), train, web |
| `app.training_examples`, `app.model_versions`, `app.generations` | P3 | agent (endpoint lookup), web |

### 8.7 Ledger schema (canonical, PROVISIONAL until IC-1)

This is the shape every service codes against. Person 1 maps the real dataset into it. If the dataset lacks something here (for example, no vendor table), Person 1 derives it if possible (e.g. vendors from transaction descriptions), documents how in `docs/DATASET.md`, and proposes any schema change to the team **before IC-1**. After IC-1 it is frozen like the rest of Section 8.

```sql
-- scar_schemas/ddl.sql (part 2 of 2) — SHARED CONTRACT, owner: Person 1
CREATE TABLE ledger.companies (
  company_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  industry TEXT,
  split TEXT NOT NULL CHECK (split IN ('train','eval'))
);

CREATE TABLE ledger.accounts (
  account_id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES ledger.companies(company_id),
  name TEXT NOT NULL,
  account_type TEXT NOT NULL CHECK (account_type IN ('asset','liability','equity','income','expense'))
);

CREATE TABLE ledger.vendors (
  vendor_id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES ledger.companies(company_id),
  name TEXT NOT NULL
);

CREATE TABLE ledger.transactions (
  txn_id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES ledger.companies(company_id),
  txn_date DATE NOT NULL,
  amount NUMERIC(14,2) NOT NULL,          -- positive = money spent (7.4)
  account_id TEXT NOT NULL REFERENCES ledger.accounts(account_id),
  vendor_id TEXT REFERENCES ledger.vendors(vendor_id),
  description TEXT,
  txn_type TEXT
);
CREATE INDEX ON ledger.transactions (company_id, txn_date);

-- ground truth for planted anomalies; NEVER readable by the agent
CREATE TABLE ledger.planted_anomalies (
  anomaly_id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL,
  kind TEXT NOT NULL,                     -- e.g. 'duplicate_payment'
  txn_ids JSONB NOT NULL,
  details JSONB NOT NULL DEFAULT '{}'
);

-- role used ONLY by the sql_query tool: three tables, current company only
CREATE ROLE scar_sql_tool NOLOGIN;
GRANT scar_sql_tool TO scar;              -- the app's DB user can SET ROLE to it
GRANT USAGE ON SCHEMA ledger TO scar_sql_tool;
GRANT SELECT ON ledger.accounts, ledger.vendors, ledger.transactions TO scar_sql_tool;
ALTER TABLE ledger.accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE ledger.vendors ENABLE ROW LEVEL SECURITY;
ALTER TABLE ledger.transactions ENABLE ROW LEVEL SECURITY;
CREATE POLICY company_only ON ledger.accounts FOR SELECT TO scar_sql_tool
  USING (company_id = current_setting('app.company_id', true));
CREATE POLICY company_only ON ledger.vendors FOR SELECT TO scar_sql_tool
  USING (company_id = current_setting('app.company_id', true));
CREATE POLICY company_only ON ledger.transactions FOR SELECT TO scar_sql_tool
  USING (company_id = current_setting('app.company_id', true));
```

The agent's `sql_query` tool must never be able to read `ledger.companies` (it holds the split), `ledger.planted_anomalies` (it holds answers), or anything in `app` (it holds expected answers). The grants above enforce that.

### 8.8 Sentry conventions

Person 2 implements these; everyone else relies on them for debugging and the demo.

**Initialization (every Python service):** `sentry_sdk.init(dsn=SENTRY_DSN, environment=SENTRY_ENVIRONMENT, traces_sample_rate=1.0, send_default_pii=True)`. Service name set as a tag `service`.

**Spans for each agent run (agent service):**

| Span | `op` | `name` | Required attributes |
|---|---|---|---|
| Run (container) | `gen_ai.invoke_agent` | `invoke_agent ledger-agent` | `gen_ai.operation.name="invoke_agent"`, `gen_ai.agent.name="ledger-agent"` |
| Each model call | `gen_ai.chat` | `chat {endpoint_model_id}` | `gen_ai.operation.name="chat"`, `gen_ai.request.model`, `gen_ai.input.messages` (JSON string), `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` |
| Each tool call | `gen_ai.execute_tool` | `execute_tool {tool_name}` | `gen_ai.operation.name="execute_tool"`, `gen_ai.tool.name`, plus tool input/output as JSON strings (exact attribute keys: VERIFY in Sentry docs) |

Attribute values must be primitives; serialize lists and dicts to JSON strings.

**Tags on every run:** `run_id`, `task_id`, `arm`, `seed`, `generation`, `purpose`, `origin`, `split`, `family`, `template_id`, `model_version`, `prompt_version`, then `passed` and `stop_reason` when the run ends. Group a run's spans as one conversation keyed by `run_id` if the Python SDK supports conversation IDs (VERIFY).

**Failures become Sentry issues.** For every failed run with `purpose` in `traffic` or `judge`, send one Sentry event (level `warning`, message `agent_run_failed`) with the tags above and fingerprint `["agent_run_failed", template_id, stop_reason]`, so Sentry groups failures into one issue per failure mode. This is the "failures Sentry catches" in our pitch and a key demo screen.

**Logs:** forward the structured logs (7.4) from all three services to Sentry Logs (enablement method: VERIFY). **Web:** Session Replay on every page, plus browser tracing with trace propagation to the agent service so a judge's click connects to the backend run (VERIFY setup).

### 8.9 Change protocol for shared contracts

Applies to `schemas/` and `docs/SPEC.md` Sections 1–10.

1. Post the proposed change in the team chat: what, why, and which services are affected.
2. Open a PR that changes `scar_schemas` and this spec together, and bumps `SCHEMA_VERSION` (minor for additive, major for breaking).
3. Get at least one approval from another person; make sure all three have seen it.
4. Merge. Everyone rebases. Each owner updates their own code.

Prefer additive changes (a new optional field with a default). After IC-1, never rename or remove a field; add a new one instead.

---

## 9. Algorithms, precisely

### 9.1 Company splits (Person 1)

Sort all `company_id`s ascending. Shuffle with `random.Random(SPLIT_SEED)`. The first `max(2, round(EVAL_COMPANY_FRACTION × n_companies))` companies are `eval`; the rest are `train`. Store in `ledger.companies.split`. Record the resulting lists in `docs/DATASET.md`. This runs once and is never re-run with a different seed. **If the dataset has fewer than 6 companies, stop and raise it with the team** before building anything else; we would need a different held-out design (D-07).

### 9.2 Agent loop (Person 2)

**System prompt `system_v1`** (file `agent/scar_agent/prompts/system_v1.md`; Person 2 may refine wording before IC-1, then it is frozen because every training example contains it):

```
You are a careful bookkeeping assistant working inside one company's accounting ledger.

You can only learn about the ledger by calling tools. Never guess numbers, IDs, or dates.

How to work:
1. Read the question carefully. Identify the accounts, vendors, and date range it refers to.
2. Use get_accounts and get_vendors to find exact IDs before filtering by them.
3. Use get_transactions or sql_query to retrieve data. Dates are inclusive and formatted YYYY-MM-DD.
4. Do arithmetic carefully. Money has two decimal places.
5. For bookkeeping tasks, record entries with create_journal_entry. Every entry must balance.
6. Finish by calling the submit_answer tool exactly once, as a tool call like every other tool:
   - numeric questions: set "number" to the answer
   - questions asking which transactions or vendors: set "ids" to the list of IDs
   - bookkeeping tasks: set "entry_ids" to the IDs returned by create_journal_entry
Never write the answer, or a submit_answer call, as plain text. The task only ends when you call the submit_answer tool.
```

Step 6 was reworded on 2026-10-18, before IC-1: with the original `submit_answer(number=...)` notation the base model (Qwen3-4B-Instruct-2507) wrote the call as plain text in 26 of 30 seed runs and passed none.

**User message:** exactly `f"Company: {company_name}\n\n{task.prompt}"`.

**Prompt loading:** `system_v1` is read from the file. Any other `prompt_version` is read from `app.prompt_versions` (`body.text`).

**Loop (per attempt):**

```
messages = [system(prompt_text), user(f"Company: {company_name}\n\n{task.prompt}")]
final_answer, stop_reason, no_tool_turns = None, None, 0
mv = registry lookup(model_version)  ->  endpoint_base_url, endpoint_model_id
for step in 1..max_steps:
    try:
        resp = openai_client(mv.endpoint_base_url).chat.completions.create(
            model=mv.endpoint_model_id,
            messages=to_openai_messages(messages),
            tools=load_tools(TOOLS_VERSION), tool_choice="auto",
            temperature=temperature, max_tokens=MAX_TOKENS_PER_TURN)
    except (timeout, connection error, 5xx):
        stop_reason = "model_error"; break
    msg = from_openai_message(resp.choices[0].message as dict); messages.append(msg)
    accumulate resp.usage tokens
    if not msg.tool_calls:
        no_tool_turns += 1
        if no_tool_turns == 1:
            messages.append(user("Call a tool, or call submit_answer to finish.")); continue
        stop_reason = "no_submit"; break
    for call in msg.tool_calls (in order):
        if call.name == "submit_answer":
            final_answer = call.arguments
            messages.append(tool(call.id, "submit_answer", '{"ok":true}'))
            stop_reason = "submitted"; break        # ignore any later calls in this message
        if "__raw__" in call.arguments:
            content = '{"error":"arguments were not valid JSON"}'
        else:
            r = POST env /tools/execute {run_id, company_id, name, arguments}
            if HTTP 503 or timeout: stop_reason = "tool_error"; break
            content = dumps(r.result) if r.ok else dumps({"error": r.error})
        if len(content) > TOOL_RESULT_MAX_CHARS:
            content = content[:TOOL_RESULT_MAX_CHARS] + "...[truncated]"
        messages.append(tool(call.id, call.name, content))
    if stop_reason: break
if stop_reason is None: stop_reason = "max_steps"
verify = POST env /verify {task_id, run_id, stop_reason, final_answer}
store RunResult(passed=verify.passed, verify_detail=verify.detail, ...)
```

Whole-run timeout is 180 s (`stop_reason="timeout"`). Tool results are JSON strings produced with `dumps_args`-style deterministic serialization. Attempts within one `RunRequest` with `n > 1` run concurrently and each gets its own `run_id` and `attempt_index`.

### 9.3 Verifier (Person 1)

If `stop_reason != "submitted"`: `passed=False`, `detail="not submitted: {stop_reason}"`. Otherwise, by `expected.answer_type`:

- **number:** `final_answer["number"]` must exist; pass if `abs(Decimal(str(x)) − expected.number) ≤ expected.tolerance`. Detail: `"number: expected {e}, got {x}"`.
- **id_set:** `final_answer["ids"]` must exist; pass if `set(ids) == set(expected.id_set)` (order and duplicates ignored). Detail lists missing and extra IDs.
- **journal_entry:** `final_answer["entry_ids"]` must exist; every ID must be in `app.sandbox_entries` with this `run_id` and the task's `company_id`. Every submitted entry must balance (total debits = total credits). Compare submitted entries to `expected.journal_entries` as a multiset of `(entry_date, sorted((account_id, debit, credit) for each line))`, with amounts compared within 0.01 and memos ignored. Pass on exact multiset match.

The verifier never calls an LLM.

### 9.4 Failure mining with Jev (Person 2)

**Which runs:** `app.runs` where `arm, seed, generation` match the `MineRequest`, `purpose` in (`traffic`, `judge`), `passed = false`, and no row exists yet in `app.failures` for that `run_id`.

**State sent to Jev** (a JSON object; keep it under about 8k tokens by truncating each `result_excerpt` to 500 characters and keeping at most 12 steps):

```json
{
  "task": {"family": "...", "template_id": "...", "question": "..."},
  "steps": [{"tool": "get_transactions", "arguments": {}, "result_excerpt": "...", "was_error": false}],
  "final_answer": {"number": 1243.5},
  "stop_reason": "submitted",
  "verifier_detail": "number: expected 1234.50, got 1243.50"
}
```

**Questions** (Python SDK form; texts are part of the contract so results are comparable across the team):

```python
from typesafe_sdk import Choice, Noul

TRIAGE_QUESTIONS = {
    "cause": Choice(
        instructions="Why did this accounting agent's run fail? Judge from `steps`, `final_answer`, `stop_reason` and `verifier_detail`.",
        criteria={
            "wrong_tool": "The agent used tools that could not answer the question, or skipped a tool it clearly needed.",
            "bad_tool_args": "The agent called a reasonable tool with wrong arguments, such as wrong dates, account, vendor, or filters.",
            "reasoning_error": "The agent retrieved the right data but computed or concluded incorrectly.",
            "wrong_final_format": "The agent had the right answer but submitted it in the wrong field or format.",
            "gave_up": "The agent stopped without submitting an answer, or never finished.",
            "environment_error": "A tool or service failed in a way the agent could not have fixed, such as timeouts or server errors.",
            "ambiguous_task": "The question itself is unclear or could reasonably be read in more than one way.",
            "other": "None of the above describes the failure.",
        },
    ),
    "learnable": Noul(
        instructions="A better model using the same tools and the same information could have answered this task correctly.",
        criteria={
            "true": "The failure came from the agent's own choices or reasoning.",
            "false": "The failure came from the environment, a broken tool, or an unanswerable question.",
        },
    ),
}
```

The exact way to pass `criteria` to `Noul` in the Python SDK should be checked against `docs.typesafe.ai/primitives` (VERIFY); the REST form `{"true": ..., "false": ...}` is verified.

**Inclusion rule:** `included = learnable_p >= INCLUDE_LEARNABLE_P and cause not in {environment_error, ambiguous_task} and stop_reason not in {model_error, tool_error, timeout}`. When excluded, `exclusion_reason` names the first rule that failed.

**Cap and order:** after triage, if more than `FAILURE_CAP_PER_GEN` are included, keep `origin == judge` first, then highest `learnable_p`, ties broken by `run_id` ascending. Records beyond the cap are stored with `included=false`, `exclusion_reason="over_cap"`.

**Idempotency:** calling `/failures/mine` twice for the same arm/seed/generation returns the existing records and makes no new Jev calls.

**Spot check:** Person 2 hand-labels 20 random triaged failures once and records Jev's agreement rate in `docs/JEV.md`.

### 9.5 Templates, generation, and planted anomalies (Person 1)

Templates are code: each has a parameter sampler, at least five phrasings, and an answer function that runs SQL over the ledger. This starting catalog is **provisional** until Person 1 has seen the dataset; the final catalog with exact SQL goes in `docs/TEMPLATES.md` before IC-1.

| template_id | family | answer_type | params | answer definition | difficulty |
|---|---|---|---|---|---|
| `agg.total_by_account_period` | aggregation | number | account_id, start_date, end_date | SUM(amount) of the company's transactions in the account, dates inclusive | 1 |
| `agg.total_by_vendor_period` | aggregation | number | vendor_id, start_date, end_date | SUM(amount) for the vendor, dates inclusive | 1 |
| `agg.count_by_account_period` | aggregation | number | account_id, start_date, end_date | COUNT of transactions (tolerance 0) | 1 |
| `agg.top_vendor_by_spend` | aggregation | id_set | start_date, end_date | vendor_id(s) with the highest SUM(amount); all tied vendors | 2 |
| `agg.mom_change_account` | aggregation | number | account_id, month | SUM(month) − SUM(previous month) | 2 |
| `agg.share_of_expense` | aggregation | number | account_id, start_date, end_date | 100 × SUM(account) ÷ SUM(all expense accounts), rounded to 2 decimals, tolerance 0.05 | 3 |
| `anom.duplicate_payments` | anomaly | id_set | start_date, end_date | txn_ids of the later transaction in every pair with same vendor_id and amount and dates ≤ 3 days apart, within the period | 2 |
| `anom.round_amounts_over` | anomaly | id_set | threshold, start_date, end_date | txn_ids with amount ≥ threshold and amount a multiple of 100 | 1 |
| `anom.vendor_outliers` | anomaly | id_set | vendor_id, start_date, end_date | txn_ids with amount > 3 × that vendor's median amount over the whole ledger | 3 |
| `book.accrual` | bookkeeping | journal_entry | expense_account_id, liability_account_id, amount, entry_date | one entry: debit expense, credit liability | 1 |
| `book.prepaid_amortization` | bookkeeping | journal_entry | prepaid_account_id, expense_account_id, total, months, start_month | one entry per month on the last day of each month: debit expense, credit prepaid, `total/months` rounded to cents, last month absorbs rounding | 2 |
| `book.reclassify` | bookkeeping | journal_entry | txn_id, to_account_id | one entry on the txn date moving the txn amount from its current account to the target account | 3 |

**Parameter sampling:** only real IDs from the task's company; date ranges inside the company's data range; reject a sample whose answer is degenerate (zero transactions, an empty id_set when the template expects matches, or more than 50 IDs) and resample, up to 20 tries.

**Train pool:** `POST /tasks/generate` with `split=train, n=TRAIN_POOL_SIZE, seed=TRAIN_POOL_SEED, origin=generated`, stratified evenly across the three families and across train companies. Positions 0..1999 go into `app.train_pool`.

**Eval set `eval_v1`:** `EVAL_SET_SIZE` tasks from eval-split companies, seed `EVAL_SET_SEED`, exactly 100 per family, difficulty spread evenly. Stored as an `EvalSet` with its sha256. **Generated once; never regenerated** without a team decision.

**Planted anomalies:** to guarantee anomaly tasks have answers, Person 1 inserts duplicate payments into every company (for example 5–15 per company: copies of real transactions with a new `txn_id`, same vendor and amount, date shifted 1–3 days) and records them in `ledger.planted_anomalies`. Answers are still computed by the SQL definitions above over the whole ledger, so natural anomalies count too. Every insertion is listed in `docs/DATASET_CHANGES.md` (QuickBooks requires disclosure).

**Simulator schedule:** `/simulate` picks `n_tasks` pool positions with `random.Random(1000 * seed + generation).sample(range(TRAIN_POOL_SIZE), n_tasks)`. The arm is deliberately not part of the seed, so every arm sees identical traffic for a given seed and generation.

### 9.6 Mutator (Person 1)

For each included failure, load its run and task. Produce `variants_per_failure` new tasks with the **same `template_id`**:

1. Sample new parameters with `random.Random(hash((failure_id, seed, i)))`. Use at least three different train-split companies across the variants, including the original company for at least two of them.
2. Keep the original difficulty for 70% of variants; use difficulty + 1 (capped at 3) for the other 30% when the template supports it. This keeps raising difficulty over generations.
3. Choose a phrasing from the template's phrasings. If `PARAPHRASE_LM_*` is configured, optionally ask it to paraphrase. Keep the paraphrase only if every parameter value that appears in the original wording still appears in it; otherwise use the template phrasing.
4. Compute the expected answer with the template's answer function. Never use an LLM for this (R5).
5. Store with `origin=variant`, `split=train`, `parent_failure_id=failure_id`.

Assert every company used is a train-split company (R6).

### 9.7 Training-data builder (Person 3)

For lineage `D-s{seed}` at generation `g`:

1. **Sampling set:** the variants created this generation plus the original failed tasks behind the included failures.
2. **Sample:** one `RunBatchRequest` with, for each task, `n=K_SAMPLES`, `temperature=SAMPLE_TEMPERATURE`, `purpose="sampling"`, the current model and `system_v1`.
3. **Keep:** passed attempts only. Per task, keep at most `MAX_EXAMPLES_PER_TASK`, preferring fewer steps, ties broken by `run_id`. Drop exact duplicates (identical tool-call sequence and final answer).
4. **Zero-pass tasks:** tasks with 0 of `K_SAMPLES` passes are logged. They go to the teacher path only if that stretch goal is enabled (Section 13, P3-12).
5. **Replay:** add `round(REPLAY_RATIO × n_self)` examples from passed `traffic` runs of earlier generations in the same lineage (train split, task_ids not already in this snapshot), chosen with `random.Random(seed * 100 + g)`.
6. **Check isolation:** assert every task in the snapshot is train-split (R6).
7. **Write** `TrainingExample` rows with `snapshot_id = new_id("snap")` and `lineage = "D-s{seed}"`. The training dataset for generation `g` is **all examples in the lineage from generations 1..g** (cumulative), deduplicated by `origin_run_id`.

### 9.8 Training and deployment (Person 3)

**Policy:** every generation trains a fresh LoRA adapter **from the base model** on the cumulative lineage dataset. This avoids drift from stacking adapters and limits forgetting.

**Starting hyperparameters** (adjust only after measuring, and record changes in `docs/DECISIONS.md`): LoRA rank 16, alpha 32, dropout 0.05, learning rate 1e-4, 2 epochs, max sequence length 4096, loss only on assistant tokens (system, user and tool tokens masked). Training seed equals the lineage seed.

**Data export:** each example as one JSONL line `{"messages": to_openai_messages(ex.messages), "tools": load_tools()}`. Whether Veri accepts this directly is **VERIFY** (F15). If not, use `training_jobs.create_custom_script` to run our own training script (for example TRL's SFT trainer with the model's chat template) on Veri GPUs; the export format stays the same.

**Veri export (decided 2026-10-18, D-03):** Veri's managed `sft_text` cannot take the line above as is (see `docs/VERI.md` section 2). For Veri jobs, each example is converted to one `{"prompt", "completion", "tools"}` row per assistant turn: `prompt` is the message list sent to the model, `completion` is the assistant message it returned, `tools` is `load_tools()` as a JSON string, assistant messages carry `"content": null` when empty, and tool-call argument strings are re-serialized with `json.dumps(json.loads(s), ensure_ascii=False)`, which is exactly how vLLM renders them at inference (R7). TRL masks the prompt, so loss is on assistant tokens only. Managed SFT uses seed 42 for every lineage. Rows longer than `max_seq_length` are dropped.

**Deploy:** preferred path is a Veri deployment with an OpenAI-compatible endpoint. Fallback is vLLM serving the base model with LoRA adapters loaded by name (F16). Either way, the result is a `ModelVersion` with `endpoint_base_url` and `endpoint_model_id` the agent can call. Before registering, send one test chat completion with tools and confirm a tool call parses.

### 9.9 Promotion gate (Person 3)

For candidate `c` with parent `p` (the current promoted version of the lineage):

1. **Eval score:** run every task in `eval_v1` once at `EVAL_TEMPERATURE`, `purpose="eval"`. `eval_score = passed / 300`. The parent's eval score is reused from its own gate (generation 0's base score is computed once and shared by all lineages).
2. **Regression set:** up to `REGRESSION_SET_SIZE` train-split tasks that `p` passed in its most recent `traffic` runs, chosen with `random.Random(seed * 100 + g)`. Run once at temperature 0, `purpose="regression"`. `regression_score = passed / size`.
3. **Rule:** promote if `eval_score(c) > eval_score(p)` **and** `regression_score(c) >= REGRESSION_MIN_PASS`. Otherwise reject; the parent stays current, and the lineage's training examples are kept for the next generation.

The same gate applies to every arm. For B and C, the "candidate" is a prompt version with the parent's weights.

### 9.10 Experiment arms (Persons 1 and 3)

| Arm | What changes each generation | Who implements the learn-step |
|---|---|---|
| **A. Frozen base** | Nothing. Evaluated once (equal to generation 0). | — |
| **B. Failure memory** | Lessons from included failures are appended to the prompt. | P1 `/learn/B` |
| **C. GEPA** | The system prompt is optimized with GEPA on the included failures. | P1 `/learn/C` |
| **D. Weight updates (ours)** | The model is retrained (9.7–9.8). | P3 |
| **E. D + C** (first to cut) | D's training, then C's learn-step on the new weights. | P3 then P1 |

All arms use identical traffic per seed and generation (9.5), the same failure triage and cap (9.4), and the same gate (9.9). Seeds `0, 1, 2` for arms C and D at minimum; arm B with as many seeds as time allows.

**Learn-step B (memory):** for each included failure, call the reflection LM once with the run's question, steps summary, verifier detail and Jev cause, asking for one lesson of at most 40 words in the form "When …, do …". Store in `app.lessons`. The candidate prompt is `system_v1` text + `"\n\nLessons from past mistakes:\n"` + the `MEMORY_MAX_LESSONS` most recent lessons for the lineage, one per line, each starting with `- `. Store as `PromptVersion` `B-s{seed}-g{generation}`.

**Learn-step C (GEPA):** use the `gepa` package with a custom adapter (F17).

- Seed candidate: `{"system_prompt": <current prompt text>}`.
- Trainset: variants of this generation's included failures (create them through `/mutate` if not already present) plus `GEPA_TRAIN_EXTRA` random train-pool tasks. Valset: `GEPA_VAL_SIZE` other train-pool tasks. Never eval tasks (R6).
- Adapter `evaluate`: store each candidate prompt as a `PromptVersion` with `is_candidate=true` and ID `C-cand-<hex>`, run the batch through agent `/agent/run_batch` with `purpose="gepa"`, score 1 for passed and 0 otherwise. When traces are requested, return per-task feedback text made of the verifier detail plus a short steps summary.
- Budget: `GEPA_MAX_METRIC_CALLS`. Reflection model: `REFLECTION_LM_*`.
- The best candidate is stored as `PromptVersion` `C-s{seed}-g{generation}` (`is_candidate=false`) and returned.

### 9.11 Eval isolation (all builders)

Every function that produces variants, training examples, GEPA sets, or lessons calls a shared check before writing:

```python
def assert_train_only(task_ids: list[str], conn) -> None:
    rows = conn.execute(
        "SELECT task_id FROM app.tasks WHERE task_id = ANY(%s) AND split <> 'train'", (task_ids,)
    ).fetchall()
    if rows:
        raise RuntimeError(f"eval leakage: {len(rows)} non-train tasks, e.g. {rows[0][0]}")
```

Person 3 adds this to `scar_schemas` as `scar_schemas.guards.assert_train_only` in Phase 0.

### 9.12 Judge flow (Person 2, using Person 1's endpoints)

1. The judge picks a **train-split** company from a dropdown (`GET env /companies?split=train`) and types a question.
2. `POST /judge/parse`: Jev `Choice` over template IDs, with each template's `description` as its criterion plus `"other": "The question does not match any template."` If the choice is `other` or confidence is below 0.5, return `valid=false` with a friendly message and three example questions.
3. Otherwise ask the parse LM (`PARSE_LM_*`) to fill the template's `params_schema`, giving it the company's account and vendor lists so it outputs real IDs. Call env `/tasks/from_params` with `dry_run=true` to validate and get the canonical `interpretation`.
4. Show the interpretation ("Total spend in Software Subscriptions for Acme from 2025-07-01 to 2025-09-30"). The judge confirms.
5. `POST /judge/run` creates the task (`origin=judge`) and runs it on the current promoted model of arm D, seed 0 (from train `GET /models/current?arm=D&seed=0`), with `purpose="judge"` and `generation` set to the latest finished generation of `D-s0` plus one, so the next generation's mining picks it up. It shows the answer, pass/fail, and a link to the Sentry trace. The expected answer is never shown before the run.
6. Judge failures are always included first in the next generation's failure budget (9.4). The run viewer has a **"Re-run on newest model"** button for the demo payoff.

### 9.13 Volume and cost sanity check (per lineage, per generation)

| Step | Agent runs | Notes |
|---|---|---|
| Traffic | 200 | `TRAFFIC_PER_GEN` |
| Sampling | up to 50 × 11 × 8 = 4,400 | cap × (10 variants + original) × `K_SAMPLES` |
| Eval | 300 | `eval_v1` |
| Regression | 100 | |
| Jev calls | ≤ ~150 | one per failed traffic/judge run |

If sampling is the bottleneck, reduce `K_SAMPLES` to 4 and `VARIANTS_PER_FAILURE` to 5 first (change protocol 8.9). If eval is the bottleneck, do not shrink `eval_v1`; reduce the number of seeds for arm B instead.

---

## 10. Phases and integration checkpoints

Times are targets; align them to the official schedule once it's posted. Each phase ends with an integration checkpoint (IC). The smoke scripts are owned by Person 3 in `scripts/`.

### Phase 0 — together (about the first hour)

| Person 1 | Person 2 | Person 3 |
|---|---|---|
| Download and inspect the dataset (P1-01). Read the full QuickBooks rules. | Create Sentry backend and web projects; get DSNs. Get a Jev key and make one test call. Read the full Sentry rules. | Create the repo skeleton, `scar_schemas` (P3-01), docker compose and DDL (P3-02). Set up Veri account and credits. Read the full Veri rules. |

All three: fill in names at the top of this spec, agree on CODEOWNERS handles, create a team chat with a `#blockers` channel.

**IC-0 passes when:** `docker compose up -d db` and `scripts/init_db.sh` succeed; every service can `import scar_schemas`; each person has their keys in `.env`; one Jev call returns an answer; the Veri account is active.

### Phase 1 — build against stubs (about 4 hours)

**Stubs due early in this phase**, so nobody waits:

- **Person 1** (about 2 hours in): 30 seed tasks in `fixtures/tasks_seed.jsonl` and `app.tasks`; `/verify`, `/tasks/{id}`, `/companies`; `/tools/execute` with at least `get_accounts` and `get_transactions`.
- **Person 2** (about 2 hours in): `/agent/run` working with `model_version="dev"`; `fixtures/runs_sample.jsonl` with at least 20 runs including at least 8 failures.
- **Person 3** (by end of phase): a `base` row in `app.model_versions` pointing at a real endpoint; one toy training job timed end to end.

**IC-1 passes when** `scripts/smoke_ic1.sh` shows: ledger loaded with row-level security working; all 30 seed tasks run on `base`; each run stored with `passed` set by the verifier; spans and tags visible in Sentry; failed runs grouped into Sentry issues; base pass rate printed. **After IC-1, the ledger schema (8.7) and the template catalog (template IDs, parameters, and answer definitions) are frozen.** Parameter sampling ranges may still be tuned in P1-07, until the train pool and `eval_v1` are generated in P1-08.

### Phase 2 — first crude loop (about 4 hours)

**IC-2 passes when** `scripts/smoke_ic2.sh` runs generation 1 of lineage `D-s0` in tiny mode (`--tiny`: traffic 20, failure cap 5, 2 variants, `K_SAMPLES` 2, first 30 tasks of `eval_v1` sorted by task_id) and produces a `GenerationRecord` ending in `promoted` or `rejected`, a registered candidate model, and training examples. Tiny-mode results are never reported.

### Phase 3 — improve in parallel (the bulk of the event, including overnight)

**IC-3 passes when** the dashboard shows at least two full-size generations for `D-s0` and at least one each for `B-s0` and `C-s0`, and the judge flow works end to end.

### Phase 4 — demo freeze (about the last 4 hours)

**IC-4 passes when** the demo has been rehearsed twice, a backup recording exists, and the Devpost submission is complete (Section 17).

### Run order for full-size experiments (Person 3 schedules)

1. Generation 0 for base (eval once, shared by all lineages).
2. `D-s0` generations 1–4.
3. `C-s0`, then `B-s0`.
4. `D-s1`, `D-s2`, `C-s1`, `C-s2`.
5. `B-s1`, `B-s2` if time allows.
6. Arm E only if everything above is done.

---

## 11. Work package: Person 1 — Environment + experiments

**Mission:** own the world the agent lives in and the yardstick it's measured by: the ledger, tasks and their correct answers, tools, verification, traffic, and the prompt-based comparison arms. Then own the write-up.

**Owns:** `env/`, `experiments/`, `fixtures/tasks_seed.jsonl`, `README.md`, `docs/DATASET.md`, `docs/DATASET_CHANGES.md`, `docs/TEMPLATES.md`, `docs/RESULTS.md`. Tables: `ledger.*`, `app.tasks`, `app.train_pool`, `app.eval_sets`, `app.sandbox_entries`, `app.prompt_versions`, `app.lessons`.

**Depends on:** `scar_schemas` and the database (Person 3, Phase 0). A registered `base` model (Person 3, end of Phase 1) for P1-07. Agent `/agent/run_batch` for `/simulate` and GEPA (Person 2, Phase 2; until then test against `fixtures/runs_sample.jsonl`). `app.failures` rows (Person 2, Phase 2; until then use `fixtures/failures_sample.jsonl`).

**Provides:** seed tasks, verifier and tools (Phase 1 stubs, then complete by IC-1); templates, train pool and `eval_v1` (Phase 2); mutator and simulator (IC-2); learn-steps B and C (IC-3).

| ID | Task | Acceptance criteria | Phase |
|---|---|---|---|
| P1-01 | Inspect the dataset with `huggingface_hub`. Write `docs/DATASET.md`: files, columns and types, row counts, number of companies, date ranges, how each raw field maps to the canonical schema (8.7), the amount sign convention, and what's missing. Update F7. | `DATASET.md` committed; team told the company count; if fewer than 6 companies, raised immediately (9.1). | 0 |
| P1-02 | Loader `env/scar_env/load_ledger.py`: idempotently drop and reload `ledger.*` from the raw files into the canonical schema, deriving missing fields as documented. Apply splits (9.1). | Row counts match `DATASET.md`; foreign keys hold; two consecutive runs produce identical data (compare table checksums). | 1 |
| P1-03 | Planted anomalies (9.5) and `docs/DATASET_CHANGES.md` listing every modification. | `ledger.planted_anomalies` populated; the duplicate-payment SQL finds every planted pair. | 1 |
| P1-04 | `/tools/execute` with the five tools in 8.4. `sql_query` runs inside a transaction that does `SET LOCAL ROLE scar_sql_tool`, `SELECT set_config('app.company_id', %s, true)`, `SET LOCAL statement_timeout = '5s'`, read-only; accepts one statement starting with `SELECT` or `WITH`; caps at 200 rows. Decimals and dates in results are strings. `create_journal_entry` validates that accounts belong to the company and that the entry balances, then writes `app.sandbox_entries`. Bad input returns `ok=false` with a message for the model. | Tests prove: other companies' rows are invisible; reading `ledger.companies`, `ledger.planted_anomalies`, or any `app.*` table through `sql_query` fails; unbalanced entries are rejected. | 1 |
| P1-05 | **Stub, early:** 30 seed tasks (10 per family, train-split companies, hand-written with code-computed answers) in `fixtures/tasks_seed.jsonl` and `app.tasks` (`origin=seed`). `/verify` (9.3), `/tasks/{id}`, `/companies`, `/health`. | Verifier tests cover all three answer types, tolerance edges, missing fields, and non-submitted runs. Person 2 can run the seed tasks. | 1 |
| P1-06 | Template catalog in code (9.5) with at least five phrasings each; `GET /templates`; `docs/TEMPLATES.md` with each template's exact SQL. | Each template produces 50 valid, non-degenerate tasks on train companies; 3 answers per template checked by hand. | 1 |
| P1-07 | Difficulty tuning: run `base` on 200 provisional train tasks; report pass rate per template in `DATASET.md`; adjust parameter ranges so overall base failure is 40–70%. **Must finish before P1-08.** | Overall base failure rate in range, written down. | 2 |
| P1-08 | `/tasks/generate`; build the train pool (2,000) and `eval_v1` (300) per 9.5. Record the eval sha256 in `DATASET.md`. | `app.train_pool` has 2,000 positions; `eval_v1` has exactly 100 tasks per family, all eval-split. | 2 |
| P1-09 | `/tasks/from_params` with `dry_run`, for the judge flow (9.12). | Rejects eval-split companies and invalid params with clear errors; returns an interpretation without the answer. | 2 |
| P1-10 | `/mutate` (9.6). | On `fixtures/failures_sample.jsonl`, produces valid train-only variants; same seed gives the same parameters. | 2 |
| P1-11 | `/simulate` (9.5 schedule), calling agent `/agent/run_batch` with `purpose="traffic"`. | Two different arms with the same seed and generation get identical pool positions. | 2 |
| P1-12 | `/learn/B` (9.10). | Produces a `PromptVersion` with at most 20 lessons; prompt under 3,000 tokens; uses train-only failures. | 3 |
| P1-13 | `/learn/C` with GEPA (9.10). Verify the adapter keyword (F17) first. | Produces a `PromptVersion`; stays within `GEPA_MAX_METRIC_CALLS`; train-only assert passes. | 3 |
| P1-14 | `experiments/report.py`: read `app.generations` and write `docs/RESULTS.md` (eval score per arm per generation: mean, min, max over seeds) and a PNG chart for Devpost. | Re-running regenerates identical output from the same data. | 3 |
| P1-15 | Devpost page, `README.md`, demo video, final `DATASET_CHANGES.md`. | Section 17 checklist items owned by Person 1 are done. | 4 |

**Person 1 must not:** edit agent or train code; write to `app.runs`, `app.failures`, or registry tables; let any LLM compute an expected answer; regenerate `eval_v1` after P1-08.

---

## 12. Work package: Person 2 — Agent + observability + web

**Mission:** own everything about a run: executing it, recording it, diagnosing failures with Jev, and showing it all to judges.

**Owns:** `agent/` (including `prompts/system_v1.md`), `web/`, `fixtures/runs_sample.jsonl`, `fixtures/failures_sample.jsonl`, `docs/JEV.md`, `docs/SENTRY.md`, `docs/DEMO.md` (except Person 3's heading). Tables: `app.runs`, `app.failures`.

**Depends on:** `scar_schemas` (Person 3, Phase 0). env `/tools/execute`, `/verify`, `/tasks/{id}` (Person 1 stubs, early Phase 1). A registered `base` model (Person 3, end of Phase 1; until then use `model_version="dev"`). env `/companies`, `/templates`, `/tasks/from_params` (Person 1, Phase 2) for the judge flow. train `/generations` and `/models` (Person 3, Phase 2) for the dashboard.

**Provides:** `/agent/run` (early Phase 1), sample runs fixture (early Phase 1), Sentry tracing (IC-1), `/agent/run_batch` and `/failures/mine` (IC-2), judge flow and web (IC-3).

| ID | Task | Acceptance criteria | Phase |
|---|---|---|---|
| P2-01 | Agent service skeleton with `/health`; registry lookup with the `dev` exception (7.5); OpenAI client per endpoint. | Calls a dev endpoint with tools and parses a tool call. | 1 |
| P2-02 | **Stub, early:** agent loop exactly per 9.2; `/agent/run`; store every run in `app.runs`; `/runs/{id}`, `/runs`. Write `system_v1.md` from 9.2. | Seed tasks run end to end. Tests with a mocked model cover every `stop_reason`, the no-tool nudge, malformed arguments, truncation, and that `to_openai_messages(from_openai_message(x))` round-trips. | 1 |
| P2-03 | Sentry backend per 8.8: init, spans, tags, failed-run issues with fingerprints. Write verified details into `docs/SENTRY.md` (F13). | A run shows nested invoke/chat/tool spans with attributes and tags; failed runs group into one issue per template and stop reason. | 1 |
| P2-04 | **Stub, early:** `fixtures/runs_sample.jsonl` (≥ 20 real runs, ≥ 8 failures across stop reasons) and a hand-written `fixtures/failures_sample.jsonl` (≥ 5 records) matching the models. | Both files validate against `RunResult` / `FailureRecord`. | 1 |
| P2-05 | `/agent/run_batch` with an async semaphore (`max_concurrency`), `n` attempts per request, results in request order then attempt order. | 200 runs at concurrency 16 finish without errors; ordering test passes. | 2 |
| P2-06 | `/failures/mine` per 9.4: state builder, Jev call with retries, inclusion rule, cap and order, idempotency. Write `docs/JEV.md` (verified SDK usage, model pin, rate limits). Replace the hand-written failures fixture with real records. | Unit tests with a mocked Jev cover inclusion, cap, ordering, and idempotency; a real call succeeds against `jev-1.14.0`. | 2 |
| P2-07 | Jev spot check: hand-label 20 random triaged failures; record agreement in `docs/JEV.md`. | Agreement rate recorded. | 2 |
| P2-08 | `/judge/parse` and `/judge/run` per 9.12. | Five sample questions parse to the right template with valid params; one unparseable question returns a friendly `valid=false`. | 3 |
| P2-09 | Web app: `/ask` (judge UI), `/runs/[run_id]` (transcript, verifier detail, Jev cause, Sentry link, "Re-run on newest model"), `/dashboard` (eval score vs. generation per arm, mean with min–max band over seeds; generations table). Session Replay and browser tracing with propagation to the agent service. | Works against live services; replays and connected traces visible in Sentry. | 3 |
| P2-10 | Sentry Logs from all three services (8.8); saved Sentry views for the demo (issues list, one trace, agent dashboard). | Logs visible in Sentry, filterable by `run_id`. | 3 |
| P2-11 | Lead demo rehearsals (Section 14); prepare 10 judge questions on train companies that the current model fails. | Two rehearsals done; question list in `docs/DEMO.md`. | 4 |

**Person 2 must not:** edit env or train code; write to tables other than `app.runs` and `app.failures`; send `Task.expected` to any model, Jev, or the browser before a run finishes; change `system_v1.md` after IC-1.

---

## 13. Work package: Person 3 — Training + loop

**Mission:** own turning data into better models and deciding whether to ship them: shared package and infrastructure, the model registry, Veri training and deployment, the promotion gate, and the loop runner for every arm.

**Owns:** `schemas/` (creates it; then shared under 8.9), `train/`, `scripts/`, `docker-compose.yml`, `.env.example`, `.github/CODEOWNERS`, `fixtures/training_examples_sample.jsonl`, `docs/VERI.md`, and the Person 3 heading in `docs/DEMO.md`. Tables: `app.training_examples`, `app.model_versions`, `app.generations`.

**Depends on:** env `/simulate`, `/mutate`, `/learn/B`, `/learn/C`, `eval_v1` (Person 1, Phases 2–3). agent `/agent/run_batch`, `/failures/mine` (Person 2, Phase 2). Until those exist, build against `fixtures/runs_sample.jsonl` and `fixtures/failures_sample.jsonl`.

**Provides:** `scar_schemas` and the database (IC-0); `base` endpoint and training-time measurement (IC-1); the loop (IC-2); full-size runs and the registry the dashboard reads (IC-3).

| ID | Task | Acceptance criteria | Phase |
|---|---|---|---|
| P3-01 | Create `scar_schemas` verbatim from Section 8 (constants, models, `openai_format` implementation, `ids`, `guards.assert_train_only`, `tools_v1.json`, `ddl.sql`) with tests. | Models round-trip through JSON; tools load; OpenAI conversion round-trips; Persons 1 and 2 import it. | 0 |
| P3-02 | `docker-compose.yml` (Postgres 16, user/password/db `scar` as the image's default superuser, so it can create the `scar_sql_tool` role), `scripts/init_db.sh` applying the DDL, `.env.example`, `CODEOWNERS`. | IC-0 database checks pass on all three laptops. | 0 |
| P3-03 | Answer every F15 question in `docs/VERI.md` with tested snippets: SFT data format (including tool calls), deployment creation, swapping versions, API key variable, GPU choices, job timing and cost. | Each answer backed by a snippet that ran. | 0–1 |
| P3-04 | Choose the base model (D-04): available on Veri, chat template supports tools, tool calls parse reliably, LoRA fits on an affordable GPU. Deploy it; register `base` (`generation=0`, `status="promoted"`). | The agent completes seed tasks on `base`; D-04 recorded. | 1 |
| P3-05 | Toy training job: build `fixtures/training_examples_sample.jsonl` from the passed runs in `runs_sample.jsonl`, train, deploy, call it. Record time and cost; decide D-03 (serving path). | Fine-tuned endpoint returns a valid tool call; timing in `VERI.md`. | 1 |
| P3-06 | train service skeleton: `/health`, `/models`, `/models/current`, `/generations`, `/generations/{id}`. | Endpoints return registry data in contract shapes. | 1 |
| P3-07 | Data builder per 9.7 with the isolation assert. | On fixtures, produces deduplicated, capped examples with replay; cumulative dataset logic tested. | 2 |
| P3-08 | Training launcher and deploy per 9.8 (Veri, with custom-script fallback; vLLM serving fallback). | One real generation's candidate trains, deploys, and passes the tool-call check. | 2 |
| P3-09 | Promotion gate per 9.9. | Rule unit-tested; eval and regression runs use the right purposes and temperature. | 2 |
| P3-10 | Loop runner per 6.3 for arms A–E: resumable state machine over `GenStatus`, `/loop/run`, `/loop/status`, `--tiny` mode; `scripts/smoke_ic1.sh` and `scripts/smoke_ic2.sh`. | IC-2 smoke passes; killing the runner mid-generation and restarting resumes at the right step. | 2 |
| P3-11 | Full-size runs in the run order (Section 10), including overnight. Watch for failures and restart. | IC-3 met; all generations recorded. | 3 |
| P3-12 | *Stretch:* teacher path for zero-pass tasks: get a teacher model's runs through the same agent harness format, verify them, add as `source="teacher"`. Check the teacher provider's terms on training with its outputs first. | Teacher examples pass the verifier; terms check noted in `DECISIONS.md`. | 3 |
| P3-13 | *Stretch:* GRPO for tasks with partial pass rates, following Veri's agent-GRPO demo (VERIFY approach). | Runs end to end on a small set. | 3 |
| P3-14 | Demo readiness: keep the latest `D-s0` endpoint warm; a fast "demo generation" procedure that trains on judge failures; backup recording of a full cycle. | Procedure written in `docs/DEMO.md` and timed. | 4 |

**Person 3 must not:** edit env or agent code; write to tables other than the three it owns; change `scar_schemas` after IC-0 without the change protocol; report tiny-mode or `dev` results.

---

## 14. Demo script (about 4 minutes; adjust to the judging format)

| Time | What happens | Who drives |
|---|---|---|
| 0:00–0:20 | Hook: "Most self-improving agents only rewrite their prompts. Ours turns every failure Sentry catches into training data, retrains the model, and only ships it if it gets better on companies it has never seen." | Person 1 |
| 0:20–1:00 | Dashboard: eval score by generation for each arm, with seed bands. Point at the gate: rejected generations don't ship. | Person 3 |
| 1:00–2:30 | Live: a judge picks a company and asks a question (use the prepared failing list if they're stuck). Show the confirmation, the run, and the failure. Open the Sentry issue and the trace. Show Jev's diagnosis in the run viewer. | Person 2 |
| 2:30–3:20 | The loop: open the latest generation record; show the variants made from one failure, the examples count, the Veri job, and the gate scores. Then click "Re-run on newest model" on an earlier judge question that the new generation fixed. | Person 3 |
| 3:20–4:00 | Close: the headline numbers, what's new versus prior art (Section 2.2), and which sponsor tools did what. | Person 1 |

**Backup:** a recorded video of the full flow (Person 3, P3-14), and screenshots of the Sentry views (Person 2). If live training can't finish during judging, show the pre-recorded cycle and the already-fixed judge questions from earlier rounds.

---

## 15. Risks and fallbacks

| Risk | Trigger | Fallback | Owner |
|---|---|---|---|
| Dataset has very few companies | Fewer than 6 companies at P1-01 | Team decides a different held-out design before building (D-07) | P1 |
| Base model too strong or too weak | Base failure outside 20–85% at P1-07 | Retune templates first; then switch base model size | P1, P3 |
| Veri can't serve or swap adapters | Not working by end of Phase 1 | Train on Veri, download adapter, serve with vLLM `--enable-lora` on a GPU box (Vultr if available) | P3 |
| Veri SFT can't take tool-call data | P3-03 finding | Run our own training script via `create_custom_script` | P3 |
| Jev unavailable or rate-limited | Errors or 429s at P2-06 | Tev1 through Ollama with the same SDK (F19); note the change in results | P2 |
| Training too slow for live demo | Toy job timing at P3-05 | Smaller model, fewer examples, pre-run generations, backup recording | P3 |
| Sampling too slow | Generation takes too long at IC-2 | Lower `K_SAMPLES` and `VARIANTS_PER_FAILURE` (8.9) | P3 |
| GEPA too expensive | Cost or time per generation too high | Lower `GEPA_MAX_METRIC_CALLS`; fewer seeds for C as a last resort | P1 |
| Model memorizes instead of learning | Train-company gains with no eval gains | That is itself a finding; report it honestly; check variant diversity | P1, P3 |
| Model games the verifier | Suspicious passes in spot checks | Tighten verifier; never compare strings when values are possible | P1 |
| Prompt methods beat weight updates | Results | Report it; the crossover and arm E are still results | P1 |
| Sentry export API unclear | P2-03 | `app.runs` is authoritative for training; Sentry stays the observability and demo layer | P2 |
| Teammate blocked | Posted in `#blockers` | Answer within 15 minutes; the blocked person works on the next task in their table meanwhile | all |

---

## 16. Decision log

Settled decisions are listed here. New decisions go in `docs/DECISIONS.md` (append-only, one line each, under your person's heading) and are folded into this table at phase boundaries.

| ID | Decision | Status |
|---|---|---|
| D-01 | Domain: accounting agent on the QuickBooks dataset | Decided |
| D-02 | Main method: filtered fine-tuning with LoRA, retrained from base each generation on cumulative lineage data | Decided |
| D-03 | Serving: one Veri multi-adapter deployment (base plus every LoRA version by `model` name); training with managed `sft_text`, one row per assistant turn (9.8); fallback custom script + vLLM | Decided 2026-10-18, pending the P3-05 smoke test |
| D-04 | Base model: `Qwen/Qwen3-4B-Instruct-2507` | Decided 2026-10-18 |
| D-05 | Postgres 16, plain SQL via psycopg, one table pattern (indexed columns + `body JSONB`) | Decided |
| D-06 | Python 3.11 + FastAPI + Pydantic v2 + uv (one project per service); Next.js for web | Decided |
| D-07 | Company-level split, 30% eval (min 2), seed 7 | Decided, unless fewer than 6 companies |
| D-08 | Jev pinned to `jev-1.14.0` | Decided |
| D-09 | Tools hide `company_id`; SQL tool isolated by row-level security | Decided |
| D-10 | Same gate for every arm; identical traffic across arms per seed and generation | Decided |
| D-11 | Arm E is the first thing cut; then GRPO; then the teacher path; GEPA is never cut | Decided |
| D-12 | `app.runs` is the authoritative training source; Sentry is for observability and the demo | Decided |

---

## 17. Submission checklist

| Item | Owner | Done |
|---|---|---|
| Devpost description: what it does, the problem, track (ML), and every company challenge entered (Sentry, Veri, QuickBooks, plus any MLH prizes) and how each was used | P1 | ☐ |
| Dataset modifications disclosed on Devpost and in `docs/DATASET_CHANGES.md` | P1 | ☐ |
| Public GitHub repo with README and setup instructions | P1 | ☐ |
| 2–3 minute demo video | P1 | ☐ |
| Results chart and table from `docs/RESULTS.md` on Devpost | P1 | ☐ |
| Sentry screenshots: issues grouped by failure mode, one agent trace, a session replay | P2 | ☐ |
| Veri job and deployment screenshots | P3 | ☐ |
| All three teammates added to the Devpost submission | P1 | ☐ |
| All three present in person for judging | all | ☐ |

---

## Appendix A — Briefing prompts for each person's AI assistant

Paste the whole spec, then the prompt for your person. Update the "current task" line as you go.

**Person 1**

```
You are the coding assistant for Person 1 (Environment + experiments) on team Scar Tissue at the LA Hacks AI Hackathon 2026. The attached SPEC.md is the single source of truth.

Before writing any code, read Sections 1, 4, 6, 7, 8 and 9, and the Person 1 work package in Section 11.

Rules you must follow:
- Only create or edit files under env/, experiments/, fixtures/tasks_seed.jsonl, README.md, docs/DATASET.md, docs/DATASET_CHANGES.md, docs/TEMPLATES.md and docs/RESULTS.md.
- Only write to the tables Person 1 owns (Section 8.6). You may read any table.
- Import every shared model and constant from scar_schemas. Never redefine them.
- Every expected answer is computed by code from the ledger. Never use an LLM for labels.
- Never let eval-split companies into variants, GEPA sets, or lessons. Use assert_train_only.
- For anything marked VERIFY in Section 4 (for example the GEPA adapter keyword), check the official docs in Appendix B or ask me. Never guess an API.
- If the spec is silent, choose the simplest option consistent with it and tell me so I can log it.

Work through tasks P1-01 to P1-15 in order. After each task, report which acceptance criteria pass.
Current task: P1-01.
```

**Person 2**

```
You are the coding assistant for Person 2 (Agent + observability + web) on team Scar Tissue at the LA Hacks AI Hackathon 2026. The attached SPEC.md is the single source of truth.

Before writing any code, read Sections 1, 4, 6, 7, 8 and 9, and the Person 2 work package in Section 12.

Rules you must follow:
- Only create or edit files under agent/, web/, fixtures/runs_sample.jsonl, fixtures/failures_sample.jsonl, docs/JEV.md, docs/SENTRY.md and docs/DEMO.md (except the Person 3 heading).
- Only write to app.runs and app.failures. You may read any table.
- Import every shared model, constant, tool definition and the OpenAI conversion functions from scar_schemas. Never redefine them.
- Implement the agent loop exactly as in Section 9.2 and the Jev triage exactly as in Section 9.4, including the question texts.
- Never send Task.expected to any model, to Jev, or to the browser before a run has finished.
- For anything marked VERIFY in Section 4 (Sentry span attribute methods, Logs setup, Session Replay, Jev model pinning), check the official docs in Appendix B or ask me. Never guess an API.
- If the spec is silent, choose the simplest option consistent with it and tell me so I can log it.

Work through tasks P2-01 to P2-11 in order. After each task, report which acceptance criteria pass.
Current task: P2-01.
```

**Person 3**

```
You are the coding assistant for Person 3 (Training + loop) on team Scar Tissue at the LA Hacks AI Hackathon 2026. The attached SPEC.md is the single source of truth.

Before writing any code, read Sections 1, 4, 6, 7, 8 and 9, and the Person 3 work package in Section 13.

Rules you must follow:
- Only create or edit files under schemas/ (only during P3-01, then via the change protocol in 8.9), train/, scripts/, docker-compose.yml, .env.example, .github/CODEOWNERS, fixtures/training_examples_sample.jsonl, docs/VERI.md and the Person 3 heading in docs/DEMO.md.
- Only write to app.training_examples, app.model_versions and app.generations. You may read any table.
- When creating scar_schemas in P3-01, copy Section 8 verbatim. Do not add, rename or remove fields.
- Training examples are the exact messages of passed runs, converted only with scar_schemas.openai_format. Never re-template transcripts.
- Never let eval-split tasks into training data. Use assert_train_only.
- For anything marked VERIFY in Section 4 (Veri SFT data format, deployments, API key variable, vLLM tool-call parser), check the official docs in Appendix B or ask me. Never guess an API.
- If the spec is silent, choose the simplest option consistent with it and tell me so I can log it.

Work through tasks P3-01 to P3-14 in order. After each task, report which acceptance criteria pass.
Current task: P3-01.
```

---

## Appendix B — References

| Topic | Link |
|---|---|
| LA Hacks AI Hackathon tracks and challenges | https://live.lahacks.com/tracks |
| Devpost page | https://la-ai-hackathon-2026.devpost.com/ |
| QuickBooks hackathon dataset | https://huggingface.co/datasets/CL251215/QuickBooks-Hackathon-2026/tree/main |
| Jev question types (Choice, Score, Noul) | https://docs.typesafe.ai/primitives |
| Jev models and versions | https://docs.typesafe.ai/models |
| Jev REST request and response example | https://docs.databricks.com/aws/en/machine-learning/model-serving/query-systemone-models |
| Tev1 (Jev-compatible fallback) on Ollama | https://ollama.com/library/tev1 |
| Sentry AI observability overview | https://sentry.io/solutions/ai-observability/ |
| Sentry Python AI agent instrumentation (span ops and attributes) | https://docs.sentry.io/platforms/python/tracing/instrumentation/custom-instrumentation/ai-agents-module/ |
| Sentry agent observability guide (sampling) | https://blog.sentry.io/ai-agent-observability-developers-guide-to-agent-monitoring/ |
| Veri | https://www.veri.studio/ |
| Veri docs | https://docs.veri.studio/ |
| Veri LA Hacks page | https://www.veri.studio/lahacks |
| vLLM LoRA serving | https://docs.vllm.ai/en/latest/features/lora/ |
| GEPA (standalone package and adapter) | https://gepa-ai.github.io/gepa/ |
| GEPA in DSPy | https://dspy.ai/api/optimizers/GEPA/overview/ |
| Prior art: AgentScope + Trinity-RFT | https://dev.to/chen115y/self-evolving-agents-a-developers-guide-40e7 |
| Prior art: GEPA vs. GRPO | https://o-mega.ai/articles/self-improving-ai-agents-the-2026-guide |
| Evaluation rigor for self-improving agents | https://arize.com/resources/self-improving-agents/ |
| LA Hacks spring 2026 gallery (Autopsy) | https://la-hacks-2026.devpost.com/project-gallery |
