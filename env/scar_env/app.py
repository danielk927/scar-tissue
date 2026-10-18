"""env service app factory (SPEC 8.5). The served instance lives in scar_env.main."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import get_settings
from .db import Database
from .errors import install_error_handlers
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
