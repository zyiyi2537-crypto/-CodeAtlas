from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine


class MemberLifecycleLockError(RuntimeError):
    """Raised when another administrator is changing an account lifecycle."""


@contextmanager
def member_lifecycle_lock(
    engine: Engine,
    timeout_seconds: int = 95,
) -> Iterator[Connection]:
    """Serialize cross-account admin mutations across application workers."""
    lock_name = "codeatlas:member-lifecycle"
    with engine.connect() as connection:
        acquired = connection.execute(
            text("SELECT GET_LOCK(:name, :timeout)"),
            {"name": lock_name, "timeout": timeout_seconds},
        ).scalar_one()
        connection.commit()
        if acquired != 1:
            raise MemberLifecycleLockError("Member lifecycle is busy")
        try:
            yield connection
        finally:
            connection.execute(
                text("SELECT RELEASE_LOCK(:name)"),
                {"name": lock_name},
            )
            connection.commit()
