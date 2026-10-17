"""Clients for the learn-steps' dependencies: agent POST /agent/run_batch, env POST /mutate (SPEC 8.5) and the
reflection LM (SPEC 7.5 REFLECTION_LM_*). Tests replace them with fakes that have the same methods."""

import math
from collections.abc import Callable
from typing import Any

import httpx
import openai
from pydantic import BaseModel
from scar_schemas.models import MutateRequest, RunBatchRequest, RunResult, Task

from .errors import DependencyError

ReflectionLM = Callable[[str | list[dict]], str]

RUN_TIMEOUT_S = 180  # whole agent run (SPEC 7.4)
MUTATE_TIMEOUT_S = 600
REFLECTION_TIMEOUT_S = 120


def _post(url: str, body: BaseModel, timeout: float) -> Any:
    try:
        response = httpx.post(url, json=body.model_dump(mode="json"), timeout=timeout)
    except httpx.TransportError as e:
        raise DependencyError(f"POST {url} failed: {e!r}") from e
    if response.status_code >= 500:
        raise DependencyError(f"POST {url} returned HTTP {response.status_code}: {response.text[:300]}")
    response.raise_for_status()
    return response.json()


class AgentClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def run_batch(self, req: RunBatchRequest) -> list[RunResult]:
        """Runs in request order then attempt order (SPEC 8.5). The timeout allows every concurrency wave a full run."""
        waves = math.ceil(sum(r.n for r in req.requests) / req.max_concurrency)
        data = _post(f"{self.base_url}/agent/run_batch", req, timeout=60 + RUN_TIMEOUT_S * waves)
        return [RunResult.model_validate(item) for item in data]


class EnvClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def mutate(self, req: MutateRequest) -> list[Task]:
        data = _post(f"{self.base_url}/mutate", req, timeout=MUTATE_TIMEOUT_S)
        return [Task.model_validate(item) for item in data]


def openai_reflection_lm(base_url: str, api_key: str, model: str) -> ReflectionLM:
    """A `(str | list[dict]) -> str` callable over an OpenAI-compatible endpoint at temperature 0 (SPEC F17)."""
    client = openai.OpenAI(base_url=base_url, api_key=api_key, timeout=REFLECTION_TIMEOUT_S)

    def complete(prompt: str | list[dict]) -> str:
        messages = [{"role": "user", "content": prompt}] if isinstance(prompt, str) else prompt
        try:
            response = client.chat.completions.create(model=model, messages=messages, temperature=0)
        except openai.APIError as e:
            raise DependencyError(f"reflection LM call failed: {e}") from e
        return response.choices[0].message.content or ""

    return complete
