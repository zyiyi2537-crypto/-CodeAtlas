from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.engine import Engine


class LlmProviderLockError(RuntimeError):
    """Raised when the LLM-provider lifecycle lock cannot be acquired."""


@contextmanager
def llm_provider_lock(
    engine: Engine,
    timeout_seconds: int = 10,
) -> Iterator[None]:
    """Serialize LLM-provider creation, tests, activation, edits, and deletion."""
    with engine.connect() as connection:
        acquired = connection.execute(
            text("SELECT GET_LOCK('codeatlas:llm-providers', :timeout)"),
            {"timeout": timeout_seconds},
        ).scalar_one()
        if acquired != 1:
            raise LlmProviderLockError("LLM provider configuration is busy")
        try:
            yield
        finally:
            connection.execute(text("SELECT RELEASE_LOCK('codeatlas:llm-providers')"))
