"""GET /generations and GET /generations/{generation_id} (SPEC 8.5); the dashboard's source."""

from fastapi import APIRouter
from scar_schemas.models import Arm, GenerationRecord

from .. import registry
from ..deps import DbDep
from ..errors import not_found

router = APIRouter()


@router.get("/generations")
def list_generations(db: DbDep, arm: Arm | None = None, seed: int | None = None) -> list[GenerationRecord]:
    with db.conn() as conn:
        return registry.list_generations(conn, arm, seed)


@router.get("/generations/{generation_id}")
def get_generation(generation_id: str, db: DbDep) -> GenerationRecord:
    with db.conn() as conn:
        rec = registry.get_generation(conn, generation_id)
    if rec is None:
        raise not_found("generation", generation_id)
    return rec
