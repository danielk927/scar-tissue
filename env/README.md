# env service (Person 1)

Ledger, tasks, tools, verifier, mutator, simulator and the arm B/C learn-steps (SPEC Sections 8.5, 9, 11).

```bash
uv sync
uv run uvicorn scar_env.app:app --port 8001   # needs DATABASE_URL (repo-root .env) with the DDL applied
uv run pytest -q                               # creates and drops its own scar_test_* database
uv run ruff check . && uv run ruff format --check .
```

Tests need a Postgres where the `DATABASE_URL` user can create databases and roles (the SPEC docker image user can).
Each router lives in its own module under `scar_env/routes/`; `app.py` already includes all of them.
Tests that need ledger data use the `ledger` fixture (`tests/synthetic.py`), never the real dataset.
