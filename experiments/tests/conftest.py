"""Test fixtures: a throwaway database per test session with the shared DDL applied (mirrors env/tests/conftest.py).

Each pytest session creates its own database (so parallel sessions never collide) and drops it at the end.
Every test starts from empty tables.
"""

import os
import uuid
from importlib import resources

import psycopg
import pytest
from dotenv import load_dotenv
from psycopg.conninfo import make_conninfo

from scar_experiments import REPO_ROOT

load_dotenv(REPO_ROOT / ".env")
ADMIN_URL = os.environ.get(
    "TEST_ADMIN_DATABASE_URL", os.environ.get("DATABASE_URL", "postgresql://scar:scar@localhost:5432/scar")
)

# The role is cluster-wide, so a second test database must not fail on CREATE ROLE.
_IDEMPOTENT_ROLE = "DO $$ BEGIN CREATE ROLE scar_sql_tool NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;"


def _ddl() -> str:
    ddl = resources.files("scar_schemas").joinpath("ddl.sql").read_text()
    assert "CREATE ROLE scar_sql_tool NOLOGIN;" in ddl
    return ddl.replace("CREATE ROLE scar_sql_tool NOLOGIN;", _IDEMPOTENT_ROLE)


@pytest.fixture(scope="session")
def db_url():
    name = f"scar_test_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    url = make_conninfo(ADMIN_URL, dbname=name)
    try:
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(_ddl())
        yield url
    finally:
        with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture(autouse=True)
def _clean_tables(request):
    if "db_url" not in request.fixturenames:
        yield
        return
    url = request.getfixturevalue("db_url")
    with psycopg.connect(url, autocommit=True) as conn:
        tables = conn.execute(
            "SELECT schemaname || '.' || tablename FROM pg_tables WHERE schemaname IN ('ledger', 'app') ORDER BY 1"
        ).fetchall()
        conn.execute("TRUNCATE " + ", ".join(t for (t,) in tables) + " CASCADE")
    yield


@pytest.fixture
def conn(db_url):
    """An autocommit connection to the test database (what the learn-steps expect)."""
    with psycopg.connect(db_url, autocommit=True) as c:
        yield c
