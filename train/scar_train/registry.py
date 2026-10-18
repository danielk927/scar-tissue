"""Model registry and generation records: app.model_versions and app.generations (SPEC 8.6, Person 3 tables).

Pattern: indexed columns plus `body JSONB` = `model.model_dump(mode="json")`; the indexed columns always equal body.
A model version's endpoint never changes once registered (the agent caches it); only `status` moves.
"""

from datetime import datetime, timezone

import psycopg
from psycopg.types.json import Jsonb
from scar_schemas.constants import BASE_PROMPT_VERSION
from scar_schemas.models import GenerationRecord, GenStatus, ModelVersion

BASE_VERSION = "base"
FINISHED = (GenStatus.promoted, GenStatus.rejected, GenStatus.failed)


def lineage(arm: str, seed: int) -> str:
    return f"{arm}-s{seed}"


def generation_id(arm: str, seed: int, generation: int) -> str:
    return f"{arm}-s{seed}-g{generation}"


def put_model_version(conn: psycopg.Connection, mv: ModelVersion) -> None:
    """Register a new model version (409-worthy IntegrityError if the version exists)."""
    conn.execute(
        "INSERT INTO app.model_versions (version, status, body, created_at) VALUES (%s, %s, %s, %s)",
        (mv.version, mv.status, Jsonb(mv.model_dump(mode="json")), mv.created_at),
    )


def set_model_status(conn: psycopg.Connection, version: str, status: str) -> ModelVersion:
    mv = get_model_version(conn, version)
    if mv is None:
        raise KeyError(f"unknown model_version {version}")
    mv = mv.model_copy(update={"status": status})
    conn.execute(
        "UPDATE app.model_versions SET status = %s, body = %s WHERE version = %s",
        (status, Jsonb(mv.model_dump(mode="json")), version),
    )
    return mv


def get_model_version(conn: psycopg.Connection, version: str) -> ModelVersion | None:
    row = conn.execute("SELECT body FROM app.model_versions WHERE version = %s", (version,)).fetchone()
    return ModelVersion.model_validate(row[0]) if row else None


def list_model_versions(conn: psycopg.Connection) -> list[ModelVersion]:
    rows = conn.execute('SELECT body FROM app.model_versions ORDER BY created_at, version COLLATE "C"').fetchall()
    return [ModelVersion.model_validate(r[0]) for r in rows]


def put_generation(conn: psycopg.Connection, rec: GenerationRecord) -> None:
    """Insert or replace one generation record (the loop runner rewrites it after every step)."""
    conn.execute(
        "INSERT INTO app.generations (generation_id, arm, seed, generation, status, body, updated_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s)"
        " ON CONFLICT (generation_id) DO UPDATE SET status = EXCLUDED.status, body = EXCLUDED.body,"
        " updated_at = EXCLUDED.updated_at",
        (
            rec.generation_id,
            rec.arm,
            rec.seed,
            rec.generation,
            rec.status.value,
            Jsonb(rec.model_dump(mode="json")),
            datetime.now(timezone.utc),
        ),
    )


def get_generation(conn: psycopg.Connection, gen_id: str) -> GenerationRecord | None:
    row = conn.execute("SELECT body FROM app.generations WHERE generation_id = %s", (gen_id,)).fetchone()
    return GenerationRecord.model_validate(row[0]) if row else None


def list_generations(
    conn: psycopg.Connection, arm: str | None = None, seed: int | None = None
) -> list[GenerationRecord]:
    rows = conn.execute(
        "SELECT body FROM app.generations WHERE (%(arm)s::text IS NULL OR arm = %(arm)s)"
        " AND (%(seed)s::int IS NULL OR seed = %(seed)s) ORDER BY arm, seed, generation",
        {"arm": arm, "seed": seed},
    ).fetchall()
    return [GenerationRecord.model_validate(r[0]) for r in rows]


def current(conn: psycopg.Connection, arm: str, seed: int) -> tuple[str, str] | None:
    """(model_version, prompt_version) the next generation and the judge UI use: those current after the latest
    finished generation of the lineage, else base + system_v1. None when no generation finished and base is not
    registered yet."""
    row = conn.execute(
        "SELECT body FROM app.generations WHERE arm = %s AND seed = %s AND status = ANY(%s)"
        " ORDER BY generation DESC LIMIT 1",
        (arm, seed, [s.value for s in FINISHED]),
    ).fetchone()
    if row:
        rec = GenerationRecord.model_validate(row[0])
        return rec.current_model_version, rec.current_prompt_version
    if get_model_version(conn, BASE_VERSION) is None:
        return None
    return BASE_VERSION, BASE_PROMPT_VERSION
