"""Training-data builder (SPEC 9.7, P3-07). Interface fixed here; bodies are filled in by P3-07.

Lineage `{arm}-s{seed}` (arm D, or E), generation g:
- build_snapshot samples the current model on `task_ids` (this generation's variants plus the original failed
  tasks), keeps passed attempts, adds replay examples, asserts train-only, and writes TrainingExample rows under a
  new snapshot_id. Idempotent per (lineage, generation): a second call returns the existing snapshot.
- lineage_examples is the cumulative training set for the lineage (all its snapshots so far), deduplicated by
  origin_run_id, in a deterministic order.
- export_jsonl writes one line per example: {"messages": to_openai_messages(ex.messages), "tools": load_tools()}
  (SPEC 9.8); messages are verbatim from the passed run (R7).
"""

from dataclasses import dataclass, field
from pathlib import Path

import psycopg
from scar_schemas.constants import K_SAMPLES, SAMPLE_TEMPERATURE
from scar_schemas.models import TrainingExample

from .clients import AgentClient


@dataclass(frozen=True)
class Snapshot:
    snapshot_id: str | None  # None when nothing was kept
    n_self: int
    n_replay: int
    zero_pass_task_ids: list[str] = field(default_factory=list)


def build_snapshot(
    conn: psycopg.Connection,
    agent: AgentClient,
    *,
    arm: str,
    seed: int,
    generation: int,
    model_version: str,
    task_ids: list[str],
    k_samples: int = K_SAMPLES,
    temperature: float = SAMPLE_TEMPERATURE,
) -> Snapshot:
    raise NotImplementedError("P3-07")


def lineage_examples(conn: psycopg.Connection, lineage: str) -> list[TrainingExample]:
    raise NotImplementedError("P3-07")


def export_jsonl(examples: list[TrainingExample], path: Path) -> int:
    raise NotImplementedError("P3-07")
