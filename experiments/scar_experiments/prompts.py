"""Prompt versions (SPEC 8.6 app.prompt_versions, 9.2 prompt loading): system_v1 lives in Person 2's file, every
other version in app.prompt_versions."""

import hashlib
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb
from scar_schemas.constants import BASE_PROMPT_VERSION
from scar_schemas.models import LearnRequest, LearnResult, PromptVersion

from . import REPO_ROOT
from .errors import NotFoundError, StateConflictError

SYSTEM_V1_PATH = REPO_ROOT / "agent" / "scar_agent" / "prompts" / "system_v1.md"


def read_system_v1(path: Path | None = None) -> str:
    """The system_v1 text as the agent loads it (trailing newlines stripped). Raises FileNotFoundError if missing."""
    path = path or SYSTEM_V1_PATH
    if not path.is_file():
        raise FileNotFoundError(f"system_v1 prompt file not found: {path} (Person 2, SPEC 9.2)")
    return path.read_text().rstrip("\n")


def get_prompt_version(conn: psycopg.Connection, prompt_version: str) -> PromptVersion | None:
    row = conn.execute("SELECT body FROM app.prompt_versions WHERE prompt_version = %s", (prompt_version,)).fetchone()
    return PromptVersion.model_validate(row[0]) if row else None


def store_prompt_version(conn: psycopg.Connection, pv: PromptVersion) -> PromptVersion:
    """Insert unless the ID already exists, and return the stored row (an existing row wins)."""
    conn.execute(
        "INSERT INTO app.prompt_versions (prompt_version, body, created_at) VALUES (%s, %s, %s)"
        " ON CONFLICT (prompt_version) DO NOTHING",
        (pv.prompt_version, Jsonb(pv.model_dump(mode="json")), pv.created_at),
    )
    stored = get_prompt_version(conn, pv.prompt_version)
    assert stored is not None
    return stored


def learned_result(conn: psycopg.Connection, prompt_version: str, req: LearnRequest) -> LearnResult | None:
    """The stored result when this learn-step already ran. A stored version with another parent is a conflict."""
    pv = get_prompt_version(conn, prompt_version)
    if pv is None:
        return None
    if pv.parent_prompt_version != req.current_prompt_version:
        raise StateConflictError(
            f"{prompt_version} was learned from {pv.parent_prompt_version}, not {req.current_prompt_version}"
        )
    return LearnResult(prompt_version=prompt_version, notes="already learned; existing prompt version returned")


def prompt_text(conn: psycopg.Connection, prompt_version: str, system_v1: str) -> str:
    if prompt_version == BASE_PROMPT_VERSION:
        return system_v1
    pv = get_prompt_version(conn, prompt_version)
    if pv is None:
        raise NotFoundError(f"unknown prompt version: {prompt_version}")
    return pv.text


def candidate_version_id(text: str) -> str:
    """GEPA candidate ID (SPEC 7.4): C-cand-<first 16 hex of sha256(text)>."""
    return f"C-cand-{hashlib.sha256(text.encode()).hexdigest()[:16]}"
