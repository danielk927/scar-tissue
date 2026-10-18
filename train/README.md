# train service (Person 3)

Model registry, training-data builder, Veri jobs, promotion gate and the loop runner for every arm (SPEC 6.3, 9.7-9.10, 13).

```bash
uv sync
uv run uvicorn scar_train.main:app --port 8003   # needs DATABASE_URL (repo-root .env) with the DDL applied
uv run pytest -q                                 # creates and drops its own scar_test_* database
uv run ruff check . && uv run ruff format --check .
```

Database setup for a fresh machine: `docker compose up -d db && scripts/init_db.sh` (or any Postgres 16+ whose user can create roles).
`scar_train/clients.py` talks to env and agent; `trainer.py` holds the training/deploy backends (Fake* only for tests and tiny smoke runs).
