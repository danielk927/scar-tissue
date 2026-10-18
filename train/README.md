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

## Overnight experiments (P3-11)

Run these from `train/`, against the machine with the real ledger, train pool, frozen `eval_v1`, registered base and service `.env`.
Set `TRAINER=veri` on the **train service process**, with `VERI_API_KEY`, `VERI_DEPLOYMENT_ID` and `MODEL_API_KEY` configured; restart that service after changing its environment.
Full-size weight-update jobs refuse the fake trainer or deployer before collecting traffic.

```bash
uv run python -m scar_train.campaign --plan-only
uv run python -m scar_train.campaign --preflight
uv run python -m scar_train.campaign --minimum --output-dir ../runs/ic3
uv run python -m scar_train.campaign --output-dir ../runs/overnight
```

The default order is base generation 0; D-s0 generations 1-4; C-s0; B-s0; D-s1; D-s2; C-s1; C-s2.
`--include-b-seeds` adds B-s1 and B-s2. `--minimum` runs the IC-3 milestone: D-s0 twice, C-s0 and B-s0 once.
Only one generation runs at a time. Previously completed generations are read from the registry and skipped.
Each lineage's generation 0 is ensured by the existing loop runner, reusing the stored base evaluation.

The driver retries transient service failures and failed jobs three times by default (`--retries`).
It saves job IDs before polling, reconnects after driver interruption, and resubmits the same generation when a train-service restart forgets an in-memory job.
A lost POST response is recovered through the service's `lineage_busy` response and existing job ID.
The eight-hour driver limit (`--max-hours`) stops polling without cancelling the server's training job.
Rerun the identical command and output directory to resume. An exhausted failure budget stays exhausted across restarts; inspect and fix the failure before intentionally selecting a new output directory or increasing `--retries`.
Keep the train service's `LOOP_RUN_DIR` and database intact: the campaign checkpoint cannot replace its per-step state or the Veri job registry.

Evidence is saved under the output directory:

- `campaign.json`: plan, connected job IDs, failure counts and completion timestamp.
- `events.jsonl`: timestamped job logs, retries, terminal generation status and eval scores.
- `<generation_id>.json`: complete registry records, including rejected candidates and gate scores.

The driver rejects tiny/fake records and requires all 300 distinct frozen eval tasks.
No CLI mode resets lineages, deletes data, creates deployments or changes shared schemas.
`--plan-only` makes no service calls, except that `--demo --plan-only` reads the registry to choose the next generation.

## Real training fixture (P3-05)

```bash
uv run python -m scar_train.fixtures validate
uv run python -m scar_train.fixtures build
uv run python -m scar_train.fixtures validate --export ../runs/toy/dataset.jsonl
```

The shipped `fixtures/training_examples_sample.jsonl` comes from Person 2's actual passed arm-D runs.
It excludes failed, eval-split, dev, other-arm and non-base-prompt runs, keeps the existing per-task cap and deduplication policy, and preserves each transcript verbatim.
This is a development fixture, not a new training run or experimental result. `build` assigns fresh example/snapshot IDs; `validate` checks provenance against the source runs and does not modify it.

For live demo commands and recording, see the Person 3 procedure in `docs/DEMO.md`.
