"""GET /companies (SPEC 8.5)."""

from typing import Literal

from fastapi import APIRouter
from psycopg.rows import dict_row

from ..deps import DbDep

router = APIRouter()


@router.get("/companies")
def list_companies(db: DbDep, split: Literal["train", "eval"] | None = None) -> list[dict]:
    with db.conn() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT company_id, name, industry, split FROM ledger.companies"
            " WHERE (%(split)s::text IS NULL OR split = %(split)s) ORDER BY company_id",
            {"split": split},
        )
        return cur.fetchall()
