"""Reads and writes for the app tables Person 1 owns that several modules share (SPEC 8.6 pattern:
indexed columns plus `body JSONB` holding `model.model_dump(mode="json")`)."""

import psycopg
from psycopg.types.json import Jsonb
from scar_schemas.models import Task


def store_task(conn: psycopg.Connection, task: Task) -> None:
    """Insert one task. Indexed columns always equal the values in `body`."""
    conn.execute(
        "INSERT INTO app.tasks (task_id, company_id, split, family, template_id, difficulty, origin,"
        " parent_failure_id, body, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            task.task_id,
            task.company_id,
            task.split.value,
            task.family.value,
            task.template_id,
            task.difficulty,
            task.origin.value,
            task.parent_failure_id,
            Jsonb(task.model_dump(mode="json")),
            task.created_at,
        ),
    )


def get_task(conn: psycopg.Connection, task_id: str) -> Task | None:
    row = conn.execute("SELECT body FROM app.tasks WHERE task_id = %s", (task_id,)).fetchone()
    return Task.model_validate(row[0]) if row else None


def get_company(conn: psycopg.Connection, company_id: str) -> dict | None:
    row = conn.execute(
        "SELECT company_id, name, industry, split FROM ledger.companies WHERE company_id = %s", (company_id,)
    ).fetchone()
    return dict(zip(("company_id", "name", "industry", "split"), row, strict=True)) if row else None
