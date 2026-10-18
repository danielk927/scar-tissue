"""Store against a real Postgres with the shared DDL. Skipped unless TEST_DATABASE_URL is set.

Uses a throwaway database: it inserts fixture rows into ledger.* and app.* tables owned by others.
Never point it at the team database.
"""

import os
from datetime import datetime, timezone

import psycopg
import pytest
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from test_mining import REQ, make_run

from scar_agent.db import Store
from scar_agent.jev import Triage
from scar_agent.mining import mine, to_record

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


@pytest.fixture
async def db():
    pool = AsyncConnectionPool(URL, min_size=1, max_size=4, open=False)
    await pool.open()
    async with pool.connection() as conn:
        await conn.execute("TRUNCATE app.runs, app.failures, app.tasks CASCADE")
        await conn.execute("DELETE FROM ledger.transactions; DELETE FROM ledger.vendors; "
                           "DELETE FROM ledger.accounts; DELETE FROM ledger.companies")
        await conn.execute("INSERT INTO ledger.companies VALUES ('company-004', 'Stitchform Apparel Co', 'x', 'train')")
        await conn.execute("INSERT INTO ledger.accounts VALUES ('company-004:a2', 'company-004', 'Rent', 'expense'), "
                           "('company-004:a1', 'company-004', 'Cash', 'asset')")
        await conn.execute("INSERT INTO ledger.vendors VALUES ('company-004:v1', 'company-004', 'Acme')")
        await conn.execute(
            "INSERT INTO app.tasks VALUES ('task_1','company-004','train','aggregation','agg.total_by_account_period',"
            "1,'seed',NULL,%s,%s)",
            (Jsonb({"prompt": "Q?", "expected": {"number": "1.00"}}), datetime.now(timezone.utc)))
    yield Store(pool)
    await pool.close()


async def test_runs_round_trip_and_filters(db):
    a, b = make_run("run_a"), make_run("run_b", passed=True, purpose="eval")
    await db.insert_run(a)
    await db.insert_run(b)
    assert await db.get_run("run_a") == a
    assert await db.get_run("nope") is None
    assert [r.run_id for r in await db.list_runs()] == ["run_a", "run_b"]
    assert [r.run_id for r in await db.list_runs(passed=True)] == ["run_b"]
    assert [r.run_id for r in await db.list_runs(purpose="traffic", task_id="task_1")] == ["run_a"]
    assert len(await db.list_runs(limit=1)) == 1


async def test_reads(db):
    assert await db.get_company_name("company-004") == "Stitchform Apparel Co"
    assert await db.get_task_meta(["task_1"]) == {"task_1": ("agg.total_by_account_period", "Q?")}
    cat = await db.get_company_catalog("company-004")
    assert [a["account_id"] for a in cat["accounts"]] == ["company-004:a1", "company-004:a2"]
    assert cat["vendors"] == [{"vendor_id": "company-004:v1", "name": "Acme"}]
    assert await db.get_prompt_text("missing") is None
    assert await db.get_model_version("missing") is None


async def test_failures_and_mining(db):
    for r in (make_run("run_1"), make_run("run_2", purpose="judge"), make_run("run_3", passed=True)):
        await db.insert_run(r)

    class Jev:
        calls = 0

        async def triage(self, state):
            Jev.calls += 1
            return Triage("bad_tool_args", 0.7, {"bad_tool_args": 0.7}, 0.8, "jev-1.14.0")

    first = await mine(REQ, db, Jev())
    assert sorted(f.run_id for f in first) == ["run_1", "run_2"] and Jev.calls == 2
    assert await db.failed_runs_to_mine("D", 0, 1) == []
    again = await mine(REQ, db, Jev())
    assert Jev.calls == 2 and [f.failure_id for f in again] == [f.failure_id for f in first]
    assert len(await db.list_failures(arm="D", seed=0, generation=1, included=True)) == 2

    await db.insert_run(make_run("run_4"))  # a failure that arrives after the first mine
    third = await mine(REQ, db, Jev())
    assert Jev.calls == 3 and sorted(f.run_id for f in third) == ["run_1", "run_2", "run_4"]

    dup = to_record(make_run("run_1"), Triage("other", 0.5, {}, 0.1, "j"), REQ)
    with pytest.raises(psycopg.errors.UniqueViolation):  # run_id is UNIQUE in app.failures; whole batch rolls back
        await db.insert_failures([dup])
