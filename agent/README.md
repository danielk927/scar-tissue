# scar_agent (Person 2)

Agent service on `:8002`: agent loop (SPEC 9.2), run storage, Sentry tracing (8.8), Jev failure mining (9.4), judge flow (9.12).

## Run

```bash
cd agent
uv sync                      # needs ../schemas (scar_schemas, Person 3) to exist
uv run uvicorn scar_agent.api:app --port 8002
```

Reads the repo-root `.env`. Needed: `DATABASE_URL`, `ENV_SERVICE_URL`, `TRAIN_SERVICE_URL`, `MODEL_API_KEY`; for development before `base` exists, `DEV_MODEL_BASE_URL`, `DEV_MODEL_ID`, `DEV_MODEL_API_KEY` (use `model_version="dev"`). Optional: `SENTRY_DSN`, `SENTRY_ENVIRONMENT`, `SENTRY_ORG` (tracing and trace links), `TYPESAFE_API_KEY`, `TYPESAFE_DEFAULT_MODEL` (mining and judge), `PARSE_LM_*` (judge). Without a Jev key the service still runs; mining and judge endpoints return 503.

## Test

```bash
uv run pytest                       # mocked model, env, Jev
TEST_DATABASE_URL=postgresql://scar:scar@localhost:55432/scar uv run pytest   # + SQL against a THROWAWAY Postgres with ddl.sql applied
uv run ruff check .
```

The DB tests truncate `app.runs`, `app.failures`, `app.tasks` and `ledger.*`. Never point `TEST_DATABASE_URL` at the team database.

## Fixtures (P2-04)

```bash
uv run python -m scar_agent.fixtures export-runs       # >= 20 runs, >= 8 failed -> fixtures/runs_sample.jsonl
uv run python -m scar_agent.fixtures export-failures   # -> fixtures/failures_sample.jsonl
uv run python -m scar_agent.fixtures validate
```
