from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import login_admin


def test_embedding_profile_never_returns_secret_like_credential_ref(
    client: TestClient, admin
) -> None:
    csrf = login_admin(client)
    response = client.post(
        "/api/v1/embedding-profiles",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "secret-rejected",
            "base_url": "https://embedding.example/v1",
            "model": "bge-m3",
            "dimension": 1024,
            "credential_ref": "sk-real-secret-1234567890",
        },
    )
    assert response.status_code == 422


def test_embedding_profile_returns_only_configured_state(
    client: TestClient, admin, monkeypatch
) -> None:
    monkeypatch.setenv("CODEATLAS_CREDENTIAL_EMBEDDING_COMPANY", "server-only-key")
    csrf = login_admin(client)
    response = client.post(
        "/api/v1/embedding-profiles",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "safe-ref",
            "base_url": "https://embedding.example/v1",
            "model": "bge-m3",
            "dimension": 1024,
            "credential_ref": "embedding-company",
        },
    )
    assert response.status_code == 201
    assert response.json()["credential_ref"] == "已配置"
    assert response.json()["credential_configured"] is True
    assert "embedding-company" not in response.text
