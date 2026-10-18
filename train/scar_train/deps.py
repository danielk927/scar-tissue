"""FastAPI dependencies shared by the routers. Use as `def route(db: DbDep): ...`."""

from typing import Annotated

from fastapi import Depends, Request

from .db import Database


def get_db(request: Request) -> Database:
    return request.app.state.db


DbDep = Annotated[Database, Depends(get_db)]
