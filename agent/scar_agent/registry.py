"""Model endpoint and prompt lookup (SPEC 7.5, 9.2)."""

from pathlib import Path

from scar_schemas.constants import BASE_PROMPT_VERSION

from .clients import Endpoint, NotFound
from .config import env, require
from .db import Store

DEV_MODEL_VERSION = "dev"
PROMPTS_DIR = Path(__file__).parent / "prompts"


def load_system_v1() -> str:
    # trailing newline dropped so `text + "\n\n..."` (arm B) composes cleanly
    return (PROMPTS_DIR / f"{BASE_PROMPT_VERSION}.md").read_text().rstrip("\n")


class Registry:
    """Caches immutable lookups: model versions and prompt versions never change once written."""

    def __init__(self, store: Store):
        self.store = store
        self._endpoints: dict[str, Endpoint] = {}
        self._prompts: dict[str, str] = {BASE_PROMPT_VERSION: load_system_v1()}
        self._company_names: dict[str, str] = {}

    async def endpoint(self, model_version: str) -> Endpoint:
        if model_version == DEV_MODEL_VERSION:
            # DEV ONLY (SPEC 7.5): never used for training, eval or results
            return Endpoint(require("DEV_MODEL_BASE_URL"), require("DEV_MODEL_ID"),
                            env("DEV_MODEL_API_KEY", "none"))
        if model_version not in self._endpoints:
            mv = await self.store.get_model_version(model_version)
            if mv is None:
                raise NotFound(f"unknown model_version {model_version}")
            self._endpoints[model_version] = Endpoint(
                mv.endpoint_base_url, mv.endpoint_model_id, env("MODEL_API_KEY", "none"))
        return self._endpoints[model_version]

    async def prompt_text(self, prompt_version: str) -> str:
        if prompt_version not in self._prompts:
            text = await self.store.get_prompt_text(prompt_version)
            if text is None:
                raise NotFound(f"unknown prompt_version {prompt_version}")
            self._prompts[prompt_version] = text
        return self._prompts[prompt_version]

    async def company_name(self, company_id: str) -> str:
        if company_id not in self._company_names:
            name = await self.store.get_company_name(company_id)
            if name is None:
                raise NotFound(f"unknown company {company_id}")
            self._company_names[company_id] = name
        return self._company_names[company_id]
