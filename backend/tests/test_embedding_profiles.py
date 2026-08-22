from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import login_admin


def test_admin_can_create_embedding_profile_without_exposing_key(client: TestClient, admin) -> None:
    csrf = login_admin(client)
    response = client.post(
        "/api/v1/embedding-profiles",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "company-bge",
            "base_url": "http://embedding.internal/v1",
            "model": "bge-m3",
            "dimension": 1024,
            "credential_ref": "embedding-company",
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["model"] == "bge-m3"
    assert "api_key" not in payload


def test_milvus_backend_is_rejected_until_implemented(client: TestClient, admin) -> None:
    csrf = login_admin(client)
    response = client.post(
        "/api/v1/embedding-profiles",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "milvus",
            "base_url": "http://milvus",
            "model": "bge-m3",
            "dimension": 1024,
            "credential_ref": "none",
            "backend": "milvus",
        },
    )
    assert response.status_code == 422
