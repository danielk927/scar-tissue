"""POST /learn/B and POST /learn/C (SPEC 9.10). The learn-steps live in scar_experiments; this module builds their
dependencies from settings. /learn/C runs GEPA (about GEPA_MAX_METRIC_CALLS agent runs) and can take many minutes."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends
from scar_experiments.clients import AgentClient, EnvClient, ReflectionLM, openai_reflection_lm
from scar_experiments.errors import DependencyError, InvalidRequestError, NotFoundError
from scar_experiments.learn_b import learn_b
from scar_experiments.learn_c import learn_c
from scar_experiments.prompts import read_system_v1
from scar_schemas.models import LearnRequest, LearnResult

from ..config import get_settings
from ..db import Database
from ..deps import DbDep
from ..errors import ApiError

router = APIRouter()


def get_reflection_lm() -> ReflectionLM:
    s = get_settings()
    if not (s.reflection_lm_base_url and s.reflection_lm_api_key and s.reflection_lm_model):
        raise ApiError(
            503,
            "reflection_lm_unconfigured",
            "set REFLECTION_LM_BASE_URL, REFLECTION_LM_API_KEY and REFLECTION_LM_MODEL",
        )
    return openai_reflection_lm(s.reflection_lm_base_url, s.reflection_lm_api_key, s.reflection_lm_model)


def get_system_v1() -> str:
    try:
        return read_system_v1()
    except FileNotFoundError as e:
        raise ApiError(503, "system_v1_missing", str(e)) from e


def get_agent_client() -> AgentClient:
    return AgentClient(get_settings().agent_service_url)


def get_env_client() -> EnvClient:
    return EnvClient(os.environ.get("ENV_SERVICE_URL", "http://localhost:8001"))


ReflectionLMDep = Annotated[ReflectionLM, Depends(get_reflection_lm)]
SystemV1Dep = Annotated[str, Depends(get_system_v1)]
AgentDep = Annotated[AgentClient, Depends(get_agent_client)]
EnvDep = Annotated[EnvClient, Depends(get_env_client)]


@contextmanager
def _learn_conn(db: Database) -> Iterator[psycopg.Connection]:
    """A dedicated autocommit connection, so a learn-step that runs for minutes commits each lesson and candidate as
    it goes instead of holding a pooled connection in one long transaction. Maps learn-step errors to SPEC 7.4."""
    try:
        with psycopg.connect(db.url, autocommit=True) as conn:
            yield conn
    except NotFoundError as e:
        raise ApiError(404, "not_found", str(e)) from e
    except InvalidRequestError as e:
        raise ApiError(422, "invalid_input", str(e)) from e
    except DependencyError as e:
        raise ApiError(503, "dependency_unavailable", str(e)) from e


@router.post("/learn/B")
def learn_memory(req: LearnRequest, db: DbDep, lm: ReflectionLMDep, system_v1: SystemV1Dep) -> LearnResult:
    with _learn_conn(db) as conn:
        return learn_b(conn, req, lm, system_v1)


@router.post("/learn/C")
def learn_gepa(
    req: LearnRequest, db: DbDep, lm: ReflectionLMDep, system_v1: SystemV1Dep, agent: AgentDep, env: EnvDep
) -> LearnResult:
    with _learn_conn(db) as conn:
        return learn_c(conn, req, lm, agent, env, system_v1)
