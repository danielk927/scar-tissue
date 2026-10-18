"""POST /mutate and POST /simulate (SPEC 9.5, 9.6)."""

from collections.abc import AsyncIterator
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends
from scar_schemas.models import MutateRequest, SimulateRequest, SimulateResult, Task

from ..config import get_settings
from ..deps import DbDep
from ..errors import ApiError
from ..mutate import ParaphraseLM, mutate, paraphrase_lm
from ..simulate import simulate

router = APIRouter()


def get_paraphrase_lm() -> ParaphraseLM | None:
    return paraphrase_lm(get_settings())


async def get_agent_client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(base_url=get_settings().agent_service_url) as client:
        yield client


@router.post("/mutate")
def post_mutate(
    req: MutateRequest, db: DbDep, lm: Annotated[ParaphraseLM | None, Depends(get_paraphrase_lm)]
) -> list[Task]:
    """Stores and returns train-split variants (SPEC 9.6), flattened in request order; idempotent per failure."""
    if req.variants_per_failure < 1:
        raise ApiError(422, "invalid_variants_per_failure", "variants_per_failure must be at least 1")
    with db.conn() as conn:
        return mutate(conn, req, lm)


@router.post("/simulate")
async def post_simulate(
    req: SimulateRequest, db: DbDep, agent: Annotated[httpx.AsyncClient, Depends(get_agent_client)]
) -> SimulateResult:
    """Runs (or resumes) one generation's traffic through the agent; can take many minutes (see scar_env.simulate)."""
    return await simulate(db, agent, req)
