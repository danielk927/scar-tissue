"""Postgres access. Person 2 writes only app.runs and app.failures (SPEC 8.6); everything else is read-only."""

from collections.abc import Callable

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from scar_schemas.models import FailureRecord, ModelVersion, RunResult

from .config import database_url


class Store:
    def __init__(self, pool: AsyncConnectionPool):
        self.pool = pool

    @classmethod
    async def open(cls) -> "Store":
        pool = AsyncConnectionPool(database_url(), min_size=1, max_size=20, open=False)
        await pool.open()
        return cls(pool)

    async def close(self) -> None:
        await self.pool.close()

    # ---------- app.runs (owner: Person 2) ----------

    async def insert_run(self, r: RunResult) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                """INSERT INTO app.runs (run_id, task_id, model_version, prompt_version, arm, seed,
                       generation, purpose, origin, split, family, passed, stop_reason, body, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (r.run_id, r.task_id, r.model_version, r.prompt_version, r.arm, r.seed,
                 r.generation, r.purpose, r.origin.value, r.split.value, r.family.value,
                 r.passed, r.stop_reason, Jsonb(r.model_dump(mode="json")), r.created_at),
            )

    async def get_run(self, run_id: str) -> RunResult | None:
        async with self.pool.connection() as conn:
            row = await (await conn.execute(
                "SELECT body FROM app.runs WHERE run_id = %s", (run_id,))).fetchone()
        return RunResult.model_validate(row[0]) if row else None

    async def list_runs(self, task_id: str | None = None, model_version: str | None = None,
                        purpose: str | None = None, passed: bool | None = None,
                        limit: int = 100) -> list[RunResult]:
        where, args = [], []
        for col, val in (("task_id", task_id), ("model_version", model_version),
                         ("purpose", purpose), ("passed", passed)):
            if val is not None:
                where.append(f"{col} = %s")
                args.append(val)
        sql = "SELECT body FROM app.runs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at, run_id LIMIT %s"
        args.append(limit)
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(sql, args)).fetchall()
        return [RunResult.model_validate(r[0]) for r in rows]

    async def failed_runs_to_mine(self, arm: str, seed: int, generation: int) -> list[RunResult]:
        """SPEC 9.4 "Which runs"."""
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(
                """SELECT r.body FROM app.runs r
                   WHERE r.arm = %s AND r.seed = %s AND r.generation = %s
                     AND r.purpose IN ('traffic', 'judge') AND r.passed = false
                     AND NOT EXISTS (SELECT 1 FROM app.failures f WHERE f.run_id = r.run_id)
                   ORDER BY r.run_id""",
                (arm, seed, generation))).fetchall()
        return [RunResult.model_validate(r[0]) for r in rows]

    # ---------- app.failures (owner: Person 2) ----------

    async def insert_failures(self, records: list[FailureRecord]) -> None:
        """All records for one mining call in one transaction."""
        async with self.pool.connection() as conn, conn.transaction():
            for f in records:
                await conn.execute(
                    """INSERT INTO app.failures (failure_id, run_id, arm, seed, generation, included, body, created_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (f.failure_id, f.run_id, f.arm, f.seed, f.generation, f.included,
                     Jsonb(f.model_dump(mode="json")), f.created_at),
                )

    async def save_mined(self, arm: str, seed: int, generation: int, new: list[FailureRecord],
                         recap: Callable[[list[FailureRecord]], list[FailureRecord]]) -> list[FailureRecord]:
        """Insert newly triaged records and re-apply the cap over the whole generation, in one transaction.
        The row lock serializes concurrent mines; a run triaged twice concurrently keeps its first record."""
        async with self.pool.connection() as conn, conn.transaction():
            for f in new:
                await conn.execute(
                    """INSERT INTO app.failures (failure_id, run_id, arm, seed, generation, included, body, created_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (run_id) DO NOTHING""",
                    (f.failure_id, f.run_id, f.arm, f.seed, f.generation, f.included,
                     Jsonb(f.model_dump(mode="json")), f.created_at),
                )
            rows = await (await conn.execute(
                """SELECT body FROM app.failures WHERE arm = %s AND seed = %s AND generation = %s
                   ORDER BY created_at, failure_id FOR UPDATE""", (arm, seed, generation))).fetchall()
            before = [FailureRecord.model_validate(r[0]) for r in rows]
            after = recap(before)
            for old, rec in zip(before, after, strict=True):
                if (old.included, old.exclusion_reason) != (rec.included, rec.exclusion_reason):
                    await conn.execute(
                        "UPDATE app.failures SET included = %s, body = %s WHERE failure_id = %s",
                        (rec.included, Jsonb(rec.model_dump(mode="json")), rec.failure_id))
        return after

    async def list_failures(self, arm: str | None = None, seed: int | None = None,
                            generation: int | None = None,
                            included: bool | None = None) -> list[FailureRecord]:
        where, args = [], []
        for col, val in (("arm", arm), ("seed", seed), ("generation", generation),
                         ("included", included)):
            if val is not None:
                where.append(f"{col} = %s")
                args.append(val)
        sql = "SELECT body FROM app.failures"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at, failure_id"
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(sql, args)).fetchall()
        return [FailureRecord.model_validate(r[0]) for r in rows]

    # ---------- reads of other owners' tables ----------

    async def get_model_version(self, version: str) -> ModelVersion | None:
        async with self.pool.connection() as conn:
            row = await (await conn.execute(
                "SELECT body FROM app.model_versions WHERE version = %s", (version,))).fetchone()
        return ModelVersion.model_validate(row[0]) if row else None

    async def get_prompt_text(self, prompt_version: str) -> str | None:
        async with self.pool.connection() as conn:
            row = await (await conn.execute(
                "SELECT body->>'text' FROM app.prompt_versions WHERE prompt_version = %s",
                (prompt_version,))).fetchone()
        return row[0] if row else None

    async def get_task_meta(self, task_ids: list[str]) -> dict[str, tuple[str, str]]:
        """task_id -> (template_id, prompt). Deliberately never selects `expected`."""
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(
                """SELECT task_id, template_id, body->>'prompt' FROM app.tasks
                   WHERE task_id = ANY(%s) ORDER BY task_id""", (task_ids,))).fetchall()
        return {r[0]: (r[1], r[2]) for r in rows}

    async def get_company_name(self, company_id: str) -> str | None:
        async with self.pool.connection() as conn:
            row = await (await conn.execute(
                "SELECT name FROM ledger.companies WHERE company_id = %s", (company_id,))).fetchone()
        return row[0] if row else None

    async def get_company_catalog(self, company_id: str) -> dict:
        """Accounts and vendors of one company, for the judge parse LM (SPEC 9.12 step 3)."""
        # dict rows on the cursor only: setting conn.row_factory would leak into the pooled connection's next user
        async with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            accounts = await (await cur.execute(
                """SELECT account_id, name, account_type FROM ledger.accounts
                   WHERE company_id = %s ORDER BY account_id""", (company_id,))).fetchall()
            vendors = await (await cur.execute(
                "SELECT vendor_id, name FROM ledger.vendors WHERE company_id = %s ORDER BY vendor_id",
                (company_id,))).fetchall()
        return {"accounts": accounts, "vendors": vendors}
