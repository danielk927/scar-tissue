# Scar Tissue

An agent that retrains itself from its own failures.
LA Hacks AI Hackathon 2026, Model Learning track.

Most self-improving agents only rewrite their prompts.
Ours turns every failure Sentry catches into training data, retrains the model, and only ships the new version if it gets better on companies it has never seen.

The source of truth for the design is [`docs/SPEC.md`](docs/SPEC.md).

## How it works

1. An accounting agent answers questions and does bookkeeping over the QuickBooks hackathon dataset, using tools that only see one company's ledger.
2. Every run is stored and traced in Sentry; code checks each answer against a value computed from the ledger (no LLM ever produces a label).
3. Jev triages each failed run and keeps the failures a better model could have avoided.
4. Each kept failure becomes new tasks from the same template with different parameters, and their answers are again computed by code.
5. The model is fine-tuned on Veri with its own passed attempts, and a new version is promoted only if it beats the old one on held-out companies without regressing.
6. We compare this against prompt-only methods (failure memory and GEPA) on the same failures, the same held-out companies and several seeds.

## Results

![Eval success rate by generation](docs/results.png)

After two generations (seed 0), the weight-update arm answers 72.7% of questions about 30 held-out companies, up from 46.3% for the frozen base (`Qwen/Qwen3-4B-Instruct-2507`).
Failure memory (8.3%) and GEPA (43.0%) both made the agent worse after one generation and were rejected.
The gate also rejected our first adapter (70.7%) for breaking tasks the base had solved; the second adapter beat the base on those too and was promoted.
Every candidate and score is in [`docs/RESULTS.md`](docs/RESULTS.md).

## Repository

| Path | What | Owner |
|---|---|---|
| `schemas/` | Shared contracts (`scar_schemas`): models, constants, tool definitions, DDL | all |
| `env/` | env service: ledger loader, task templates, tools, verifier, mutator, traffic simulator | Person 1 |
| `experiments/` | Prompt-only baselines (arm B memory, arm C GEPA) and the results report | Person 1 |
| `agent/`, `web/` | Agent loop, Sentry instrumentation, Jev failure mining, judge UI | Person 2 |
| `train/`, `scripts/` | Model registry, training data, Veri jobs, promotion gate, loop runner | Person 3 |
| `fixtures/` | Shared sample data, including the 30 seed tasks | all |
| `docs/` | Spec, decisions, dataset notes, data changes, template catalog | all |

## Setup

Requirements: Python 3.11+ with [uv](https://docs.astral.sh/uv/), and Postgres 16+ where the app user can create roles.

```bash
cp .env.example .env    # or create .env with at least DATABASE_URL; never commit it
psql "$DATABASE_URL" -f schemas/scar_schemas/ddl.sql

cd env && uv sync
# load the dataset (download: https://huggingface.co/datasets/CL251215/QuickBooks-Hackathon-2026, then extract)
uv run python -m scar_env.load_ledger --data-dir <path>/hackathon-100
uv run python -m scar_env.seed_tasks load
uv run uvicorn scar_env.main:app --port 8001
```

Tests: `uv run pytest -q` in `schemas/`, `env/` and `experiments/` (each env test session creates and drops its own database).

## Data

We use the QuickBooks hackathon dataset (100 synthetic companies, one year of books).
Its structure and how we map it into our ledger are in [`docs/DATASET.md`](docs/DATASET.md).
Every change we made to the data, and why, is disclosed in [`docs/DATASET_CHANGES.md`](docs/DATASET_CHANGES.md).
The task templates and their exact answer SQL are in [`docs/TEMPLATES.md`](docs/TEMPLATES.md).
