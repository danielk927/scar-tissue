"""Settings for the env service, read from the repo-root .env (SPEC 7.5) and the process environment."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(REPO_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    database_url: str
    agent_service_url: str
    sentry_dsn: str | None
    sentry_environment: str
    reflection_lm_base_url: str | None
    reflection_lm_api_key: str | None
    reflection_lm_model: str | None
    paraphrase_lm_base_url: str | None
    paraphrase_lm_api_key: str | None
    paraphrase_lm_model: str | None


def _opt(name: str) -> str | None:
    return os.environ.get(name) or None


def get_settings() -> Settings:
    return Settings(
        database_url=os.environ.get("DATABASE_URL", "postgresql://scar:scar@localhost:5432/scar"),
        agent_service_url=os.environ.get("AGENT_SERVICE_URL", "http://localhost:8002"),
        sentry_dsn=_opt("SENTRY_DSN"),
        sentry_environment=os.environ.get("SENTRY_ENVIRONMENT", "hackathon"),
        reflection_lm_base_url=_opt("REFLECTION_LM_BASE_URL"),
        reflection_lm_api_key=_opt("REFLECTION_LM_API_KEY"),
        reflection_lm_model=_opt("REFLECTION_LM_MODEL"),
        paraphrase_lm_base_url=_opt("PARAPHRASE_LM_BASE_URL"),
        paraphrase_lm_api_key=_opt("PARAPHRASE_LM_API_KEY"),
        paraphrase_lm_model=_opt("PARAPHRASE_LM_MODEL"),
    )
