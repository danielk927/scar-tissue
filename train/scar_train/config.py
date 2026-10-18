"""Settings for the train service, read from the repo-root .env (SPEC 7.5) and the process environment."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(REPO_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    database_url: str
    env_service_url: str
    agent_service_url: str
    sentry_dsn: str | None
    sentry_environment: str
    base_model_hf_id: str | None
    model_api_key: str | None


def _opt(name: str) -> str | None:
    return os.environ.get(name) or None


def get_settings() -> Settings:
    return Settings(
        database_url=os.environ.get("DATABASE_URL", "postgresql://scar:scar@localhost:5432/scar"),
        env_service_url=os.environ.get("ENV_SERVICE_URL", "http://localhost:8001"),
        agent_service_url=os.environ.get("AGENT_SERVICE_URL", "http://localhost:8002"),
        sentry_dsn=_opt("SENTRY_DSN"),
        sentry_environment=os.environ.get("SENTRY_ENVIRONMENT", "hackathon"),
        base_model_hf_id=_opt("BASE_MODEL_HF_ID"),
        model_api_key=_opt("MODEL_API_KEY"),
    )
