"""HTTP clients for the env and train services, and OpenAI clients per model endpoint."""

from dataclasses import dataclass

import httpx
from openai import AsyncOpenAI
from scar_schemas.models import (
    FromParamsRequest,
    FromParamsResult,
    Task,
    TemplateSpec,
    ToolExecuteRequest,
    ToolExecuteResult,
    VerifyRequest,
    VerifyResult,
)

from .config import env, env_service_url, train_service_url

TOOL_TIMEOUT_S = 10.0     # SPEC 7.4
VERIFY_TIMEOUT_S = 10.0
MODEL_TIMEOUT_S = 60.0


class NotFound(Exception):
    pass


class Unavailable(Exception):
    """A dependency (env, train, model endpoint, Jev) is down. Maps to HTTP 503."""


class ToolInfraError(Exception):
    """env /tools/execute failed at the infrastructure level (503, timeout, connection)."""


def _raise_for(resp: httpx.Response, what: str) -> None:
    if resp.status_code == 404:
        raise NotFound(f"{what}: not found")
    if resp.status_code >= 400:
        raise Unavailable(f"{what}: HTTP {resp.status_code} {resp.text[:200]}")


class EnvClient:
    def __init__(self, base_url: str | None = None, http: httpx.AsyncClient | None = None):
        self.http = http or httpx.AsyncClient(base_url=base_url or env_service_url(), timeout=30.0)

    async def close(self) -> None:
        await self.http.aclose()

    async def get_task(self, task_id: str) -> Task:
        try:
            resp = await self.http.get(f"/tasks/{task_id}")
        except httpx.HTTPError as e:
            raise Unavailable(f"env /tasks: {e!r}") from e
        _raise_for(resp, f"task {task_id}")
        return Task.model_validate(resp.json())

    async def execute_tool(self, req: ToolExecuteRequest) -> ToolExecuteResult:
        try:
            resp = await self.http.post("/tools/execute", json=req.model_dump(mode="json"),
                                        timeout=TOOL_TIMEOUT_S)
        except httpx.HTTPError as e:
            raise ToolInfraError(repr(e)) from e
        if resp.status_code != 200:
            raise ToolInfraError(f"HTTP {resp.status_code}")
        return ToolExecuteResult.model_validate(resp.json())

    async def verify(self, req: VerifyRequest) -> VerifyResult:
        try:
            resp = await self.http.post("/verify", json=req.model_dump(mode="json"),
                                        timeout=VERIFY_TIMEOUT_S)
        except httpx.HTTPError as e:
            raise Unavailable(f"env /verify: {e!r}") from e
        _raise_for(resp, "verify")
        return VerifyResult.model_validate(resp.json())

    async def get_templates(self) -> list[TemplateSpec]:
        try:
            resp = await self.http.get("/templates")
        except httpx.HTTPError as e:
            raise Unavailable(f"env /templates: {e!r}") from e
        _raise_for(resp, "templates")
        return [TemplateSpec.model_validate(t) for t in resp.json()]

    async def get_companies(self, split: str | None = None) -> list[dict]:
        try:
            resp = await self.http.get("/companies", params={"split": split} if split else None)
        except httpx.HTTPError as e:
            raise Unavailable(f"env /companies: {e!r}") from e
        _raise_for(resp, "companies")
        return resp.json()

    async def from_params(self, req: FromParamsRequest) -> FromParamsResult:
        try:
            resp = await self.http.post("/tasks/from_params", json=req.model_dump(mode="json"))
        except httpx.HTTPError as e:
            raise Unavailable(f"env /tasks/from_params: {e!r}") from e
        if resp.status_code == 422:
            return FromParamsResult(task=None, interpretation="", valid=False,
                                    error=f"invalid parameters: {resp.text[:300]}")
        _raise_for(resp, "from_params")
        return FromParamsResult.model_validate(resp.json())


class TrainClient:
    def __init__(self, base_url: str | None = None, http: httpx.AsyncClient | None = None):
        self.http = http or httpx.AsyncClient(base_url=base_url or train_service_url(), timeout=30.0)

    async def close(self) -> None:
        await self.http.aclose()

    async def current(self, arm: str, seed: int) -> dict:
        try:
            resp = await self.http.get("/models/current", params={"arm": arm, "seed": seed})
        except httpx.HTTPError as e:
            raise Unavailable(f"train /models/current: {e!r}") from e
        _raise_for(resp, "models/current")
        return resp.json()

    async def generations(self, arm: str, seed: int) -> list[dict]:
        try:
            resp = await self.http.get("/generations", params={"arm": arm, "seed": seed})
        except httpx.HTTPError as e:
            raise Unavailable(f"train /generations: {e!r}") from e
        _raise_for(resp, "generations")
        return resp.json()


# ---------- model endpoints ----------

@dataclass(frozen=True)
class Endpoint:
    base_url: str
    model_id: str
    api_key: str


_openai_clients: dict[tuple[str, str], AsyncOpenAI] = {}


def openai_client(ep: Endpoint) -> AsyncOpenAI:
    key = (ep.base_url, ep.api_key)
    if key not in _openai_clients:
        _openai_clients[key] = AsyncOpenAI(base_url=ep.base_url, api_key=ep.api_key,
                                           timeout=MODEL_TIMEOUT_S, max_retries=2)
    return _openai_clients[key]


def parse_lm_endpoint() -> Endpoint | None:
    base_url, model = env("PARSE_LM_BASE_URL"), env("PARSE_LM_MODEL")
    if not base_url or not model:
        return None
    return Endpoint(base_url, model, env("PARSE_LM_API_KEY", "none"))
