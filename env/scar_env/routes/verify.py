"""POST /verify (SPEC 9.3)."""

import psycopg
from fastapi import APIRouter
from scar_schemas.models import VerifyRequest, VerifyResult

from ..deps import DbDep
from ..errors import ApiError, not_found
from ..log import get_logger
from ..store import get_task
from ..verify import verify

router = APIRouter()
log = get_logger(__name__)


@router.post("/verify")
def verify_run(req: VerifyRequest, db: DbDep) -> VerifyResult:
    """Unknown task -> 404. Database failure -> 503."""
    try:
        with db.conn() as conn:
            task = get_task(conn, req.task_id)
            if task is None:
                raise not_found("task", req.task_id)
            return verify(conn, task, req)
    except psycopg.Error as e:
        log.exception("verify failed", extra={"run_id": req.run_id, "task_id": req.task_id})
        raise ApiError(503, "db_unavailable", f"database error: {e}") from e
