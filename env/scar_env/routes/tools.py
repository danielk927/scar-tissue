"""POST /tools/execute (SPEC 8.4, 8.5, P1-04)."""

import psycopg
from fastapi import APIRouter
from scar_schemas.models import ToolExecuteRequest, ToolExecuteResult

from ..deps import DbDep
from ..errors import ApiError, not_found
from ..log import get_logger
from ..store import get_company
from ..tools import execute

router = APIRouter()
log = get_logger(__name__)


@router.post("/tools/execute")
def execute_tool(req: ToolExecuteRequest, db: DbDep) -> ToolExecuteResult:
    """Unknown company -> 404. Bad tool or arguments -> 200 with ok=false. Database failure -> 503."""
    try:
        with db.conn() as conn:
            if get_company(conn, req.company_id) is None:
                raise not_found("company", req.company_id)
            return execute(conn, req)
    except psycopg.errors.QueryCanceled as e:
        log.warning("tool %s timed out", req.name, extra={"run_id": req.run_id})
        raise ApiError(503, "tool_timeout", f"{req.name} exceeded the 10 s tool timeout") from e
    except psycopg.Error as e:
        log.exception("tool %s failed", req.name, extra={"run_id": req.run_id})
        raise ApiError(503, "db_unavailable", f"database error: {e}") from e
