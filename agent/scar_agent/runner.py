"""Run requests and batches (SPEC 8.5 /agent/run, /agent/run_batch). Resolves, runs, stores."""

import asyncio

from scar_schemas.models import RunRequest, RunResult

from .clients import EnvClient, openai_client
from .db import Store
from .loop import AttemptContext, run_attempt
from .registry import Registry


class Runner:
    def __init__(self, store: Store, registry: Registry, env: EnvClient, llm_factory=openai_client):
        self.store, self.registry, self.env = store, registry, env
        self.llm_factory = llm_factory

    async def _context(self, req: RunRequest) -> AttemptContext:
        task = await self.env.get_task(req.task_id)
        endpoint = await self.registry.endpoint(req.model_version)
        return AttemptContext(
            req=req,
            task=task,
            company_name=await self.registry.company_name(task.company_id),
            prompt_text=await self.registry.prompt_text(req.prompt_version),
            endpoint_model_id=endpoint.model_id,
            llm=self.llm_factory(endpoint),
            env=self.env,
        )

    async def _attempt(self, ctx: AttemptContext, i: int, sem: asyncio.Semaphore) -> RunResult:
        async with sem:
            result = await run_attempt(ctx, i)
        await self.store.insert_run(result)
        return result

    async def run(self, req: RunRequest, sem: asyncio.Semaphore | None = None) -> list[RunResult]:
        """`n` attempts of one request, concurrently, returned in attempt order."""
        sem = sem or asyncio.Semaphore(req.n)
        ctx = await self._context(req)
        return list(await asyncio.gather(*(self._attempt(ctx, i, sem) for i in range(req.n))))

    async def run_batch(self, reqs: list[RunRequest], max_concurrency: int) -> list[RunResult]:
        """Flattened in request order, then attempt order. Concurrency is counted in attempts."""
        sem = asyncio.Semaphore(max_concurrency)
        per_request = await asyncio.gather(*(self.run(r, sem) for r in reqs))
        return [r for results in per_request for r in results]
