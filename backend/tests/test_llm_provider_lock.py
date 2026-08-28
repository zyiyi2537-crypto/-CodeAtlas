from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from codeatlas.api import router
from codeatlas.llm_provider_lock import LlmProviderLockError, llm_provider_lock
from tests.conftest import login_admin


def test_llm_provider_named_lock_serializes_connections(application) -> None:
    with llm_provider_lock(application.state.engine):
        with ThreadPoolExecutor(max_workers=1) as executor:
            blocked = executor.submit(
                _try_llm_provider_lock,
                application.state.engine,
            ).result(timeout=10)
    assert blocked is True

    with llm_provider_lock(application.state.engine, timeout_seconds=0):
        pass


def _try_llm_provider_lock(engine) -> bool:
    try:
        with llm_provider_lock(engine, timeout_seconds=0):
            return False
    except LlmProviderLockError:
        return True


def test_llm_provider_mutation_routes_use_lifecycle_lock() -> None:
    expected = {
        ("/api/v1/llm/providers", "POST"),
        ("/api/v1/llm/providers/{provider_id}", "PATCH"),
        ("/api/v1/llm/providers/{provider_id}", "DELETE"),
        ("/api/v1/llm/providers/{provider_id}/test", "POST"),
        ("/api/v1/llm/providers/sync", "POST"),
        ("/api/v1/llm/providers/{provider_id}/activate", "POST"),
    }
    found: set[tuple[str, str]] = set()
    for route in router.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        if not hasattr(route, "dependant"):
            continue
        dependencies = {
            getattr(dependency.call, "__name__", "")
            for dependency in route.dependant.dependencies
        }
        if "require_llm_provider_mutation_lock" not in dependencies:
            continue
        for method in methods:
            found.add((path, method))

    assert expected <= found


def test_unauthenticated_request_cannot_acquire_llm_provider_lock(
    client: TestClient, monkeypatch
) -> None:
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("unauthenticated request acquired the LLM lifecycle lock")

    monkeypatch.setattr("codeatlas.api.llm_provider_lock", fail_if_called)

    response = client.delete("/api/v1/llm/providers/provider-1")

    assert response.status_code == 401


def test_request_without_csrf_cannot_acquire_llm_provider_lock(
    client: TestClient, admin, monkeypatch
) -> None:
    login_admin(client)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("request without CSRF acquired the LLM lifecycle lock")

    monkeypatch.setattr("codeatlas.api.llm_provider_lock", fail_if_called)

    response = client.delete("/api/v1/llm/providers/provider-1")

    assert response.status_code == 403
