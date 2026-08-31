from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine


class ChatSessionLockError(RuntimeError):
    """Raised when a chat session is busy with another turn or deletion."""


def chat_lock_name(session_id: str) -> str:
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:40]
    return f"codeatlas:chat-session:{digest}"


def acquire_chat_session_lock(
    connection: Connection,
    session_id: str,
    timeout_seconds: int = 95,
) -> str:
    lock_name = chat_lock_name(session_id)
    acquired = connection.execute(
        text("SELECT GET_LOCK(:name, :timeout)"),
        {"name": lock_name, "timeout": timeout_seconds},
    ).scalar_one()
    if acquired != 1:
        raise ChatSessionLockError("Chat session is busy")
    return lock_name


def release_chat_session_lock(connection: Connection, lock_name: str) -> None:
    connection.execute(
        text("SELECT RELEASE_LOCK(:name)"),
        {"name": lock_name},
    )


@contextmanager
def chat_session_lock(
    engine: Engine,
    session_id: str,
    timeout_seconds: int = 95,
) -> Iterator[Connection]:
    """Serialize turns and deletion for one chat session across workers."""
    with engine.connect() as connection:
        lock_name = acquire_chat_session_lock(
            connection, session_id, timeout_seconds
        )
        connection.commit()
        try:
            yield connection
        finally:
            release_chat_session_lock(connection, lock_name)
            connection.commit()
