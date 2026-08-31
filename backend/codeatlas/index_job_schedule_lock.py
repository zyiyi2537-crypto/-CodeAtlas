from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine


class IndexJobScheduleLockError(RuntimeError):
    """Raised when the global index-job scheduling lock cannot be acquired."""


@contextmanager
def index_job_schedule_connection_lock(
    connection: Connection,
    timeout_seconds: int = 10,
) -> Iterator[None]:
    acquired = connection.execute(
        text("SELECT GET_LOCK('codeatlas:index-job-schedule', :timeout)"),
        {"timeout": timeout_seconds},
    ).scalar_one()
    if acquired != 1:
        raise IndexJobScheduleLockError("Index job scheduling is busy")
    try:
        yield
    finally:
        connection.execute(text("SELECT RELEASE_LOCK('codeatlas:index-job-schedule')"))


@contextmanager
def index_job_schedule_lock(
    engine: Engine,
    timeout_seconds: int = 10,
) -> Iterator[None]:
    """Serialize job creation with embedding-profile activation across workers."""
    with engine.connect() as connection:
        with index_job_schedule_connection_lock(connection, timeout_seconds):
            yield
