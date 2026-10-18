"""GET /models and GET /models/current (SPEC 8.5)."""

from fastapi import APIRouter
from scar_schemas.models import Arm, ModelVersion

from .. import registry
from ..deps import DbDep
from ..errors import ApiError

router = APIRouter()


@router.get("/models")
def list_models(db: DbDep) -> list[ModelVersion]:
    with db.conn() as conn:
        return registry.list_model_versions(conn)


@router.get("/models/current")
def current_model(db: DbDep, arm: Arm, seed: int) -> dict:
    with db.conn() as conn:
        found = registry.current(conn, arm, seed)
    if found is None:
        raise ApiError(
            503, "base_not_registered", "no generation has finished and model version 'base' is not registered"
        )
    return {"model_version": found[0], "prompt_version": found[1]}
