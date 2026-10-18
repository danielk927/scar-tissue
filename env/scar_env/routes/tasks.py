"""Task routes (SPEC 8.5): GET /templates, POST /tasks/generate, POST /tasks/from_params,
GET /tasks/{task_id}, GET /eval_sets/{eval_set_id}."""

import json
import random

from fastapi import APIRouter
from psycopg.types.json import Jsonb
from scar_schemas.constants import EVAL_SET_ID
from scar_schemas.models import (
    EvalSet,
    FromParamsRequest,
    FromParamsResult,
    GenerateRequest,
    Origin,
    Split,
    Task,
    TaskFamily,
    TemplateSpec,
)

from ..deps import DbDep
from ..errors import ApiError, not_found
from ..store import get_company, get_task, store_task
from ..tasks import GenerationError, eval_set, generate_tasks, interpretation, make_task
from ..templates import TEMPLATES, ParamError, get_template

router = APIRouter()

GENERATE_SPLIT = {Origin.generated: Split.train, Origin.eval: Split.eval}


@router.get("/templates")
def list_templates() -> list[TemplateSpec]:
    return [t.spec for t in TEMPLATES.values()]


@router.get("/tasks/{task_id}")
def read_task(task_id: str, db: DbDep) -> Task:
    with db.conn() as conn:
        task = get_task(conn, task_id)
    if task is None:
        raise not_found("task", task_id)
    return task


@router.get("/eval_sets/{eval_set_id}")
def read_eval_set(eval_set_id: str, db: DbDep) -> EvalSet:
    with db.conn() as conn:
        row = conn.execute("SELECT body FROM app.eval_sets WHERE eval_set_id = %s", (eval_set_id,)).fetchone()
    if row is None:
        raise not_found("eval set", eval_set_id)
    return EvalSet.model_validate(row[0])


@router.post("/tasks/generate")
def generate(req: GenerateRequest, db: DbDep) -> list[Task]:
    """origin=generated builds the train pool (positions 0..n-1); origin=eval builds eval_v1. Each happens once."""
    if req.origin not in GENERATE_SPLIT:
        raise ApiError(422, "invalid_origin", f"generate makes origin generated or eval tasks, not {req.origin.value}")
    if req.split != GENERATE_SPLIT[req.origin]:
        raise ApiError(
            422, "split_origin_mismatch", f"origin {req.origin.value} requires split {GENERATE_SPLIT[req.origin].value}"
        )
    if req.n < 1:
        raise ApiError(422, "invalid_n", "n must be at least 1")
    if req.origin == Origin.eval and (req.family is not None or req.n % len(TaskFamily)):
        raise ApiError(422, "invalid_eval_request", "eval sets cover all families equally: no family, n divisible by 3")
    with db.conn() as conn:
        if req.origin == Origin.generated and conn.execute("SELECT 1 FROM app.train_pool LIMIT 1").fetchone():
            raise ApiError(409, "train_pool_exists", "app.train_pool already has rows")
        if (
            req.origin == Origin.eval
            and conn.execute("SELECT 1 FROM app.eval_sets WHERE eval_set_id = %s", (EVAL_SET_ID,)).fetchone()
        ):
            raise ApiError(409, "eval_set_exists", f"{EVAL_SET_ID} already exists and is never regenerated")
        try:
            tasks = generate_tasks(conn, split=req.split, n=req.n, seed=req.seed, origin=req.origin, family=req.family)
        except GenerationError as exc:
            raise ApiError(422, "cannot_generate", str(exc)) from None
        for task in tasks:
            store_task(conn, task)
        if req.origin == Origin.generated:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO app.train_pool (position, task_id) VALUES (%s, %s)",
                    [(i, t.task_id) for i, t in enumerate(tasks)],
                )
        else:
            es = eval_set([t.task_id for t in tasks])
            conn.execute(
                "INSERT INTO app.eval_sets (eval_set_id, body) VALUES (%s, %s)",
                (es.eval_set_id, Jsonb(es.model_dump(mode="json"))),
            )
    return tasks


@router.post("/tasks/from_params")
def from_params(req: FromParamsRequest, db: DbDep) -> FromParamsResult:
    """Judge tasks (SPEC 9.12): train-split companies only; dry_run validates and describes without storing."""
    if req.origin != Origin.judge:
        raise ApiError(422, "invalid_origin", f"from_params makes origin judge tasks, not {req.origin.value}")
    with db.conn() as conn:
        try:
            company = get_company(conn, req.company_id)
            if company is None:
                raise ParamError(f"unknown company_id: {req.company_id}")
            if company["split"] != Split.train.value:
                raise ParamError(f"{req.company_id} is an eval-split company; judge tasks must use a train company")
            template = get_template(req.template_id)
            seed = f"judge:{req.template_id}:{req.company_id}:{json.dumps(req.params, sort_keys=True, default=str)}"
            task = make_task(
                conn,
                req.template_id,
                req.company_id,
                req.params,
                origin=Origin.judge,
                difficulty=template.difficulty,
                rng=random.Random(seed),
                prompt=req.prompt,
            )
        except ParamError as exc:
            return FromParamsResult(task=None, interpretation="", valid=False, error=str(exc))
        text = interpretation(conn, task)
        if req.dry_run:
            return FromParamsResult(task=None, interpretation=text, valid=True)
        store_task(conn, task)
    return FromParamsResult(task=task, interpretation=text, valid=True)
