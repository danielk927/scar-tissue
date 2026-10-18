# env service (Person 1)

Ledger, tasks, tools, verifier, mutator, simulator and the arm B/C learn-steps (SPEC Sections 8.5, 9, 11).

```bash
uv sync
uv run uvicorn scar_env.main:app --port 8001  # needs DATABASE_URL (repo-root .env) with the DDL applied
uv run pytest -q                              # creates and drops its own scar_test_* database
uv run ruff check . && uv run ruff format --check .

# reload the ledger from the raw dataset (P1-02); prints row counts and checksums
uv run python -m scar_env.load_ledger --data-dir ~/scar-data/quickbooks-hackathon-2026/extracted/hackathon-100 \
  --changes-doc ../docs/DATASET_CHANGES.md
SCAR_REAL_DATA_TESTS=1 uv run pytest -q tests/test_load_ledger.py  # opt-in real-data test, about 80 s
```

Tests need a Postgres where the `DATABASE_URL` user can create databases and roles (the SPEC docker image user can).
Each router lives in its own module under `scar_env/routes/`; `app.py` already includes all of them.
Tests that need ledger data use the `ledger` fixture (`tests/synthetic.py`), never the real dataset.
