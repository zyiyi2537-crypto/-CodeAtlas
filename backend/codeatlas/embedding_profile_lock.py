from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.engine import Engine


class EmbeddingProfileLockError(RuntimeError):
    """Raised when the embedding-profile lifecycle lock cannot be acquired."""


@contextmanager
def embedding_profile_lock(
    engine: Engine,
    timeout_seconds: int = 10,
) -> Iterator[None]:
    """Serialize embedding-profile activation, edits, and deletion across workers."""
    with engine.connect() as connection:
        acquired = connection.execute(
            text("SELECT GET_LOCK('codeatlas:embedding-profiles', :timeout)"),
            {"timeout": timeout_seconds},
        ).scalar_one()
        if acquired != 1:
            raise EmbeddingProfileLockError("Embedding profile configuration is busy")
        try:
            yield
        finally:
            connection.execute(
                text("SELECT RELEASE_LOCK('codeatlas:embedding-profiles')")
            )
