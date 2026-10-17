"""Environment variables (SPEC 7.5). One `.env` at the repo root, loaded once."""

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")

SERVICE_NAME = "agent"


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    return value if value != "" else default


def require(name: str) -> str:
    value = env(name)
    if value is None:
        raise RuntimeError(f"missing required environment variable {name}")
    return value


def database_url() -> str:
    return require("DATABASE_URL")


def env_service_url() -> str:
    return env("ENV_SERVICE_URL", "http://localhost:8001")


def train_service_url() -> str:
    return env("TRAIN_SERVICE_URL", "http://localhost:8003")
