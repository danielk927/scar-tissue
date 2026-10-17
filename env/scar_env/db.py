"""Postgres access: one connection pool per app, plain SQL via psycopg 3 (SPEC D-05)."""

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg_pool import ConnectionPool


class Database:
    def __init__(self, url: str, max_size: int = 20):
        self.url = url
        self.pool = ConnectionPool(url, min_size=1, max_size=max_size, open=False)

    def open(self) -> None:
        self.pool.open(wait=True)

    def close(self) -> None:
        self.pool.close()

    @contextmanager
    def conn(self) -> Iterator[psycopg.Connection]:
        """A pooled connection inside a transaction: commits on success, rolls back on error."""
        with self.pool.connection() as conn:
            yield conn
