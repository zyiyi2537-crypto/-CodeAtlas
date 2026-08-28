from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from codeatlas.api import router
from codeatlas.embedding_profile_lock import (
    EmbeddingProfileLockError,
    embedding_profile_lock,
)
from tests.conftest import login_admin


def test_embedding_profile_named_lock_serializes_connections(application) -> None:
    with embedding_profile_lock(application.state.engine):
        with ThreadPoolExecutor(max_workers=1) as executor:
            blocked = executor.submit(
                _try_embedding_profile_lock,
                application.state.engine,
            ).result(timeout=10)
    assert blocked is True

    with embedding_profile_lock(application.state.engine, timeout_seconds=0):
        pass


def _try_embedding_profile_lock(engine) -> bool:
    try:
        with embedding_profile_lock(engine, timeout_seconds=0):
            return False
    except EmbeddingProfileLockError:
        return True


def test_embedding_profile_mutation_routes_use_lifecycle_lock() -> None:
    expected = {
        ("/api/v1/embedding-profiles", "POST"),
        ("/api/v1/embedding-profiles/probe", "POST"),
        ("/api/v1/embedding-profiles/{profile_id}", "PATCH"),
        ("/api/v1/embedding-profiles/{profile_id}", "DELETE"),
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
        if "require_embedding_profile_mutation_lock" not in dependencies:
            continue
        for method in methods:
            found.add((path, method))

    assert expected <= found
    activation = next(
        route
        for route in router.routes
        if getattr(route, "path", "")
        == "/api/v1/embedding-profiles/{profile_id}/activate"
    )
    activation_dependencies = {
        getattr(dependency.call, "__name__", "")
        for dependency in activation.dependant.dependencies
    }
    assert "require_embedding_activation_locks" in activation_dependencies


def test_unauthenticated_request_cannot_acquire_embedding_profile_lock(
    client: TestClient, monkeypatch
) -> None:
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("unauthenticated request acquired the lifecycle lock")

    monkeypatch.setattr("codeatlas.api.embedding_profile_lock", fail_if_called)

    response = client.delete("/api/v1/embedding-profiles/profile-1")

    assert response.status_code == 401


def test_request_without_csrf_cannot_acquire_embedding_profile_lock(
    client: TestClient, admin, monkeypatch
) -> None:
    login_admin(client)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("request without CSRF acquired the lifecycle lock")

    monkeypatch.setattr("codeatlas.api.embedding_profile_lock", fail_if_called)

    response = client.delete("/api/v1/embedding-profiles/profile-1")

    assert response.status_code == 403
