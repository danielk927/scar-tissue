"""Build and validate the P3-05 fixture from actual, verified training-company runs."""

import argparse
import sys
from collections import defaultdict
from pathlib import Path

from scar_schemas.constants import BASE_PROMPT_VERSION, TOOLS_VERSION
from scar_schemas.ids import new_id
from scar_schemas.models import RunResult, TrainingExample

from .config import REPO_ROOT
from .data_builder import _example, _keep, export_jsonl

RUNS_PATH = REPO_ROOT / "fixtures/runs_sample.jsonl"
EXAMPLES_PATH = REPO_ROOT / "fixtures/training_examples_sample.jsonl"


def read_runs(path: Path) -> list[RunResult]:
    return [RunResult.model_validate_json(line) for line in path.read_text().splitlines() if line.strip()]


def fixture_examples(runs: list[RunResult]) -> list[TrainingExample]:
    eligible = [
        run
        for run in runs
        if run.passed
        and run.split == "train"
        and run.arm == "D"
        and run.purpose in ("traffic", "sampling")
        and run.model_version != "dev"
        and run.prompt_version == BASE_PROMPT_VERSION
        and run.tools_version == TOOLS_VERSION
        and run.stop_reason == "submitted"
    ]
    if not eligible:
        raise ValueError("no passed arm-D train runs with system_v1 and tools_v1")
    lineages = {f"{run.arm}-s{run.seed}" for run in eligible}
    if len(lineages) != 1:
        raise ValueError("a training fixture must contain exactly one lineage")
    snapshot_id = new_id("snap")
    grouped: dict[str, list[RunResult]] = defaultdict(list)
    for run in eligible:
        grouped[run.task_id].append(run)
    return [
        _example(run, snapshot_id, next(iter(lineages)), "self", run.created_at)
        for task_id in sorted(grouped)
        for run in _keep(grouped[task_id])
    ]


def validate_fixture(runs: list[RunResult], examples: list[TrainingExample]) -> None:
    expected = {example.origin_run_id for example in fixture_examples(runs)}
    if len(examples) != len(expected) or {example.origin_run_id for example in examples} != expected:
        raise ValueError("fixture does not match the deduplicated, capped passed training runs")
    if len({example.example_id for example in examples}) != len(examples):
        raise ValueError("duplicate example IDs")
    if len({example.snapshot_id for example in examples}) != 1:
        raise ValueError("fixture examples must share one snapshot")
    originals = {run.run_id: run for run in runs}
    for example in examples:
        run = originals[example.origin_run_id]
        if (
            example.messages != run.messages
            or example.task_id != run.task_id
            or example.lineage != f"{run.arm}-s{run.seed}"
            or example.source != "self"
            or example.prompt_version != run.prompt_version
            or example.tools_version != run.tools_version
        ):
            raise ValueError(f"{example.example_id}: transcript or provenance differs from the passed run")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["build", "validate"])
    parser.add_argument("--runs", type=Path, default=RUNS_PATH)
    parser.add_argument("--examples", type=Path, default=EXAMPLES_PATH)
    parser.add_argument("--export", type=Path, help="also write the canonical SFT JSONL export")
    args = parser.parse_args(argv)
    try:
        runs = read_runs(args.runs)
        if args.command == "build":
            examples = fixture_examples(runs)
            args.examples.parent.mkdir(parents=True, exist_ok=True)
            args.examples.write_text(
                "".join(example.model_dump_json() + "\n" for example in examples), encoding="utf-8"
            )
        else:
            examples = [
                TrainingExample.model_validate_json(line)
                for line in args.examples.read_text().splitlines()
                if line.strip()
            ]
        validate_fixture(runs, examples)
        if args.export:
            args.export.parent.mkdir(parents=True, exist_ok=True)
            export_jsonl(examples, args.export)
        print(f"{args.command}: {len(examples)} verified examples from {len(runs)} real runs")
        return 0
    except (OSError, ValueError) as error:
        print(f"fixture error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
