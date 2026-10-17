"""env service app (SPEC 8.5, port 8001). Run: uv run uvicorn scar_env.app:app --port 8001"""

from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI

from .config import get_settings
from .db import Database
from .errors import install_error_handlers
from .log import setup_logging
from .routes import companies, learn, loop, tasks, tools, verify


def create_app(database_url: str | None = None) -> FastAPI:
    settings = get_settings()
    db = Database(database_url or settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.open()
        yield
        db.close()

    app = FastAPI(title="scar-env", lifespan=lifespan)
    app.state.db = db
    install_error_handlers(app)

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    for module in (companies, tasks, tools, verify, loop, learn):
        app.include_router(module.router)
    return app


def _init_observability() -> None:
    setup_logging()
    settings = get_settings()
    if settings.sentry_dsn:
        # SPEC 8.8 initialization; Person 2 owns the conventions.
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.sentry_environment,
            traces_sample_rate=1.0,
            send_default_pii=True,
        )
        sentry_sdk.set_tag("service", "env")


_init_observability()
app = create_app()
