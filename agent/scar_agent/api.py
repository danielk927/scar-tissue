"""Agent service, :8002 (SPEC 8.5)."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from scar_schemas.models import (
    FailureRecord,
    JudgeParseRequest,
    JudgeParseResult,
    JudgeRunRequest,
    JudgeRunResult,
    MineRequest,
    RunBatchRequest,
    RunRequest,
    RunResult,
)

from . import mining
from . import observability as obs
from .clients import EnvClient, NotFound, TrainClient, Unavailable
from .db import Store
from .jev import Jev
from .judge import Judge, JudgeError
from .log import setup_logging
from .registry import Registry
from .runner import Runner


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    obs.init_sentry()
    store = await Store.open()
    env, train = EnvClient(), TrainClient()
    runner = Runner(store, Registry(store), env)
    app.state.store, app.state.runner = store, runner
    app.state.jev = None  # created lazily so the service starts without TYPESAFE_API_KEY
    app.state.judge_deps = (store, env, train, runner)
    yield
    await env.close()
    await train.close()
    await store.close()


app = FastAPI(title="scar-agent", lifespan=lifespan)


def _jev(request: Request) -> Jev:
    if request.app.state.jev is None:
        try:
            request.app.state.jev = Jev()
        except Exception as e:
            raise Unavailable(f"jev client: {e!r}") from e
    return request.app.state.jev


def _error(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "detail": detail})


@app.exception_handler(NotFound)
async def _not_found(_: Request, e: NotFound):
    return _error(404, "not_found", str(e))


@app.exception_handler(Unavailable)
async def _unavailable(_: Request, e: Unavailable):
    return _error(503, "dependency_unavailable", str(e))


@app.exception_handler(JudgeError)
async def _judge_error(_: Request, e: JudgeError):
    return _error(422, "invalid_judge_request", str(e))


@app.get("/health")
async def health():
    return {"ok": True}


def _continue_trace(request: Request) -> None:
    """Judge runs join the caller's trace (only used when purpose == "judge"; see observability)."""
    headers = {k: v for k in ("sentry-trace", "baggage") if (v := request.headers.get(k))}
    obs.incoming_trace_headers.set(headers or None)


@app.post("/agent/run", response_model=list[RunResult])
async def agent_run(req: RunRequest, request: Request):
    _continue_trace(request)
    return await request.app.state.runner.run(req)


@app.post("/agent/run_batch", response_model=list[RunResult])
async def agent_run_batch(req: RunBatchRequest, request: Request):
    return await request.app.state.runner.run_batch(req.requests, req.max_concurrency)


@app.get("/runs/{run_id}", response_model=RunResult)
async def get_run(run_id: str, request: Request):
    run = await request.app.state.store.get_run(run_id)
    if run is None:
        raise NotFound(f"run {run_id}")
    return run


@app.get("/runs", response_model=list[RunResult])
async def list_runs(request: Request, task_id: str | None = None, model_version: str | None = None,
                    purpose: str | None = None, passed: bool | None = None, limit: int = 100):
    return await request.app.state.store.list_runs(task_id, model_version, purpose, passed, limit)


@app.post("/failures/mine", response_model=list[FailureRecord])
async def failures_mine(req: MineRequest, request: Request):
    return await mining.mine(req, request.app.state.store, _jev(request))


@app.get("/failures", response_model=list[FailureRecord])
async def list_failures(request: Request, arm: str | None = None, seed: int | None = None,
                        generation: int | None = None, included: bool | None = None):
    return await request.app.state.store.list_failures(arm, seed, generation, included)


def _judge(request: Request) -> Judge:
    store, env, train, runner = request.app.state.judge_deps
    return Judge(store, env, train, _jev(request), runner)


@app.post("/judge/parse", response_model=JudgeParseResult)
async def judge_parse(req: JudgeParseRequest, request: Request):
    return await _judge(request).parse(req)


@app.post("/judge/run", response_model=JudgeRunResult)
async def judge_run(req: JudgeRunRequest, request: Request):
    _continue_trace(request)
    return await _judge(request).run(req)
