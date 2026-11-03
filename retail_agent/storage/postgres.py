"""One place that knows how to connect to the application PostgreSQL.

Reports, user memory and the Golden index share this database. Each feature
receives a connection factory instead of reaching into another feature's
repository. Failures surface as StorageError with a fixed message: driver
errors may contain connection details and are never shown or logged.
"""

import os
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row


class StorageError(RuntimeError):
    """Controlled storage failure; the outcome of a write is unknown."""


STORAGE_UNAVAILABLE = "Application storage is unavailable or the operation failed. No success is confirmed."


def connection_factory(*, user: str | None = None, password: str | None = None, statement_timeout_ms: int = 5000):
    """Return a context-manager factory yielding a dict-row connection.

    Settings are read from the environment when a connection is opened, so
    tests and CLI flags can change them without rebuilding objects. The
    REPORTS_POSTGRES_* names are kept for backwards-compatible .env files.
    """

    @contextmanager
    def connection():
        try:
            with psycopg.connect(
                host=os.getenv("REPORTS_POSTGRES_HOST", "127.0.0.1"),
                port=int(os.getenv("REPORTS_POSTGRES_PORT", "5433")),
                dbname=os.getenv("REPORTS_POSTGRES_DATABASE", "retail_agent"),
                user=user or os.getenv("REPORTS_POSTGRES_USER", "retail_agent"),
                password=password or os.getenv("REPORTS_POSTGRES_PASSWORD", "local-reports-demo"),
                connect_timeout=3, options=f"-c statement_timeout={statement_timeout_ms} -c timezone=UTC",
                row_factory=dict_row,
            ) as conn:
                yield conn
        except (psycopg.Error, ValueError):
            raise StorageError(STORAGE_UNAVAILABLE) from None

    return connection


def golden_ingest_connection_factory():
    """Writer role for the offline Golden ingestion job; the chat agent only reads."""
    return connection_factory(
        user=os.getenv("GOLDEN_INGEST_POSTGRES_USER", "golden_ingest"),
        password=os.getenv("GOLDEN_INGEST_POSTGRES_PASSWORD", "local-golden-ingest-demo"),
        statement_timeout_ms=15000,
    )
