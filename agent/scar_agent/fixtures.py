"""Export and validate the shared fixtures (P2-04).

  python -m scar_agent.fixtures export-runs [--purpose traffic]  # app.runs -> fixtures/runs_sample.jsonl
  python -m scar_agent.fixtures export-failures  # app.failures -> fixtures/failures_sample.jsonl
  python -m scar_agent.fixtures validate         # both files parse as RunResult / FailureRecord
"""

import argparse
import asyncio
import json
import sys
from collections import Counter

from scar_schemas.models import FailureRecord, RunResult

from .config import REPO_ROOT
from .db import Store

FIXTURES = REPO_ROOT / "fixtures"
RUNS_FILE = FIXTURES / "runs_sample.jsonl"
FAILURES_FILE = FIXTURES / "failures_sample.jsonl"
MIN_RUNS, MIN_FAILED_RUNS, MIN_FAILURES = 20, 8, 5


def _write(path, models) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for m in models:
            f.write(json.dumps(m.model_dump(mode="json"), sort_keys=True) + "\n")


async def export_runs(limit: int, purpose: str | None) -> None:
    store = await Store.open()
    try:
        runs = await store.list_runs(purpose=purpose, limit=limit)
    finally:
        await store.close()
    failed = [r for r in runs if not r.passed]
    if len(runs) < MIN_RUNS or len(failed) < MIN_FAILED_RUNS:
        sys.exit(f"need >= {MIN_RUNS} runs with >= {MIN_FAILED_RUNS} failures; have {len(runs)} / {len(failed)}")
    _write(RUNS_FILE, runs)
    print(f"wrote {len(runs)} runs ({len(failed)} failed) to {RUNS_FILE}")
    print("stop reasons:", dict(Counter(r.stop_reason for r in runs)))


async def export_failures() -> None:
    store = await Store.open()
    try:
        failures = await store.list_failures()
    finally:
        await store.close()
    if len(failures) < MIN_FAILURES:
        sys.exit(f"need >= {MIN_FAILURES} failure records; have {len(failures)}")
    _write(FAILURES_FILE, failures)
    print(f"wrote {len(failures)} failures to {FAILURES_FILE}")


def validate() -> None:
    ok = True
    for path, model, minimum in ((RUNS_FILE, RunResult, MIN_RUNS), (FAILURES_FILE, FailureRecord, MIN_FAILURES)):
        if not path.exists():
            print(f"MISSING {path}")
            ok = False
            continue
        rows = [model.model_validate_json(line) for line in path.read_text().splitlines() if line.strip()]
        extra = ""
        if model is RunResult:
            failed = [r for r in rows if not r.passed]
            extra = f", {len(failed)} failed, stop reasons {dict(Counter(r.stop_reason for r in rows))}"
            ok &= len(failed) >= MIN_FAILED_RUNS
        ok &= len(rows) >= minimum
        print(f"{path.name}: {len(rows)} valid {model.__name__}{extra}")
    if FAILURES_FILE.exists() and RUNS_FILE.exists():
        run_ids = {json.loads(line)["run_id"] for line in RUNS_FILE.read_text().splitlines() if line.strip()}
        orphans = [json.loads(line)["run_id"] for line in FAILURES_FILE.read_text().splitlines()
                   if line.strip() and json.loads(line)["run_id"] not in run_ids]
        if orphans:
            print(f"note: {len(orphans)} failure records point at runs not in runs_sample.jsonl")
    sys.exit(0 if ok else 1)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    runs = sub.add_parser("export-runs")
    runs.add_argument("--limit", type=int, default=60)
    runs.add_argument("--purpose", help="only runs with this purpose, e.g. traffic (the ones /failures/mine triages)")
    sub.add_parser("export-failures")
    sub.add_parser("validate")
    args = parser.parse_args()
    if args.cmd == "export-runs":
        asyncio.run(export_runs(args.limit, args.purpose))
    elif args.cmd == "export-failures":
        asyncio.run(export_failures())
    else:
        validate()


if __name__ == "__main__":
    main()
