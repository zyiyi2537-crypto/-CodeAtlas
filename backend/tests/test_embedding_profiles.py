from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from codeatlas.models import IndexJob, Repository
from tests.conftest import login_admin


def test_admin_can_create_embedding_profile_without_exposing_key(
    client: TestClient, admin, monkeypatch
) -> None:
    monkeypatch.setenv("CODEATLAS_CREDENTIAL_EMBEDDING_COMPANY", "server-only-key")
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


def test_admin_can_activate_embedding_profile_and_queue_reindex(
    client: TestClient, application, admin, monkeypatch
) -> None:
    monkeypatch.setenv("CODEATLAS_CREDENTIAL_EMBEDDING_COMPANY", "server-only-key")
    csrf = login_admin(client)
    created = client.post(
        "/api/v1/embedding-profiles",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "active-bge",
            "base_url": "http://embedding.internal/v1",
            "model": "bge-m3",
            "dimension": 128,
            "credential_ref": "embedding-company",
        },
    )
    assert created.status_code == 201
    original_search = application.state.knowledge_search
    monkeypatch.setattr(
        "codeatlas.api.EmbeddingClient.probe_dimension", lambda _self: 128
    )
    activated = client.post(
        f"/api/v1/embedding-profiles/{created.json()['id']}/activate",
        headers={"X-CSRF-Token": csrf},
    )
    assert activated.status_code == 200
    assert activated.json()["is_active"] is True
    assert activated.json()["queued_jobs"] == 0
    assert application.state.knowledge_search is original_search
    assert application.state.external_sync.knowledge_search is application.state.knowledge_search
    assert application.state.knowledge_search.vector_store.namespace == created.json()["id"]


def test_embedding_activation_pins_reindex_to_repository_commit(
    client: TestClient, application, admin, monkeypatch
) -> None:
    monkeypatch.setenv("CODEATLAS_CREDENTIAL_EMBEDDING_COMPANY", "server-only-key")
    with Session(application.state.engine) as session:
        repository = Repository(
            name="public-snapshot",
            git_url="https://github.com/example/public-snapshot.git",
            branch="main",
            visibility="public",
            status="ready",
            last_commit="a" * 40,
            created_by=admin.id,
        )
        session.add(repository)
        session.commit()
        session.refresh(repository)
        repository_id = repository.id

    csrf = login_admin(client)
    created = client.post(
        "/api/v1/embedding-profiles",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "commit-pinned-bge",
            "base_url": "http://embedding.internal/v1",
            "model": "bge-m3",
            "dimension": 128,
            "credential_ref": "embedding-company",
        },
    )
    monkeypatch.setattr(
        "codeatlas.api.EmbeddingClient.probe_dimension", lambda _self: 128
    )
    submitted: list[str] = []
    monkeypatch.setattr(
        application.state.job_queue,
        "submit",
        lambda job_ids: submitted.extend(job_ids),
    )

    activated = client.post(
        f"/api/v1/embedding-profiles/{created.json()['id']}/activate",
        headers={"X-CSRF-Token": csrf},
    )

    assert activated.status_code == 200
    assert activated.json()["queued_jobs"] == 1
    assert len(submitted) == 1
    with Session(application.state.engine) as session:
        job = session.exec(
            select(IndexJob).where(IndexJob.repository_id == repository_id)
        ).one()
    assert job.commit == "a" * 40


def test_embedding_activation_requires_server_credential(client: TestClient, admin) -> None:
    csrf = login_admin(client)
    created = client.post(
        "/api/v1/embedding-profiles",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "missing-key",
            "base_url": "http://embedding.internal/v1",
            "model": "bge-m3",
            "dimension": 128,
            "credential_ref": "not-configured",
        },
    )
    activated = client.post(
        f"/api/v1/embedding-profiles/{created.json()['id']}/activate",
        headers={"X-CSRF-Token": csrf},
    )
    assert activated.status_code == 422
    assert "CODEATLAS_CREDENTIAL_NOT_CONFIGURED" in activated.text


def test_embedding_activation_rejects_running_external_sync(
    client: TestClient, application, admin, monkeypatch
) -> None:
    monkeypatch.setenv("CODEATLAS_CREDENTIAL_EMBEDDING_COMPANY", "server-only-key")
    csrf = login_admin(client)
    created = client.post(
        "/api/v1/embedding-profiles",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "blocked-by-sync",
            "base_url": "http://embedding.internal/v1",
            "model": "bge-m3",
            "dimension": 128,
            "credential_ref": "embedding-company",
        },
    )
    application.state.external_sync.running_sources.add("source-running")
    monkeypatch.setattr(
        "codeatlas.api.EmbeddingClient.probe_dimension", lambda _self: 128
    )

    response = client.post(
        f"/api/v1/embedding-profiles/{created.json()['id']}/activate",
        headers={"X-CSRF-Token": csrf},
    )

    application.state.external_sync.running_sources.clear()
    assert response.status_code == 409
    assert "external" in response.text.lower()


def test_embedding_context_refreshes_before_job_submission_failure(
    client: TestClient, application, admin, monkeypatch
) -> None:
    monkeypatch.setenv("CODEATLAS_CREDENTIAL_EMBEDDING_COMPANY", "server-only-key")
    csrf = login_admin(client)
    created = client.post(
        "/api/v1/embedding-profiles",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "refresh-before-submit",
            "base_url": "http://embedding.internal/v1",
            "model": "bge-m3",
            "dimension": 128,
            "credential_ref": "embedding-company",
        },
    )
    monkeypatch.setattr(
        "codeatlas.api.EmbeddingClient.probe_dimension", lambda _self: 128
    )
    monkeypatch.setattr(
        application.state.job_queue,
        "submit",
        lambda _job_ids: (_ for _ in ()).throw(RuntimeError("queue unavailable")),
    )

    with pytest.raises(RuntimeError, match="queue unavailable"):
        client.post(
            f"/api/v1/embedding-profiles/{created.json()['id']}/activate",
            headers={"X-CSRF-Token": csrf},
        )

    assert application.state.knowledge_search.vector_store.namespace == created.json()["id"]
    assert application.state.external_sync.embedding_switch_in_progress is False


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


def test_admin_can_create_tencent_multimodal_profile(
    client: TestClient, admin, monkeypatch
) -> None:
    monkeypatch.setenv("CODEATLAS_CREDENTIAL_TENCENT_KINFRA", "server-only-key")
    csrf = login_admin(client)

    response = client.post(
        "/api/v1/embedding-profiles",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "tencent-kinfra",
            "base_url": "https://tokenhub.tencentmaas.com/v1",
            "model": "kinfra-vl-embedding-2b",
            "dimension": 2048,
            "credential_ref": "tencent-kinfra",
            "provider": "tencent_multimodal",
        },
    )

    assert response.status_code == 201
    assert response.json()["provider"] == "tencent_multimodal"


def test_admin_can_probe_tencent_embedding_dimension(
    client: TestClient, admin, monkeypatch
) -> None:
    monkeypatch.setenv("CODEATLAS_CREDENTIAL_TENCENT_KINFRA", "server-only-key")
    csrf = login_admin(client)

    class ProbeClient:
        def __init__(self, settings):
            assert settings.embedding_mode == "tencent_multimodal"

        def probe_dimension(self):
            return 2048

    monkeypatch.setattr("codeatlas.api.EmbeddingClient", ProbeClient)

    response = client.post(
        "/api/v1/embedding-profiles/probe",
        headers={"X-CSRF-Token": csrf},
        json={
            "base_url": "https://tokenhub.tencentmaas.com/v1",
            "model": "kinfra-vl-embedding-2b",
            "credential_ref": "tencent-kinfra",
            "provider": "tencent_multimodal",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"dimension": 2048}


def test_activation_probes_dimension_before_creating_collection(
    client: TestClient, admin, monkeypatch
) -> None:
    monkeypatch.setenv("CODEATLAS_CREDENTIAL_TENCENT_KINFRA", "server-only-key")
    csrf = login_admin(client)
    created = client.post(
        "/api/v1/embedding-profiles",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "wrong-dimension",
            "base_url": "https://tokenhub.tencentmaas.com/v1",
            "model": "kinfra-vl-embedding-2b",
            "dimension": 1024,
            "credential_ref": "tencent-kinfra",
            "provider": "tencent_multimodal",
        },
    )

    monkeypatch.setattr(
        "codeatlas.api.EmbeddingClient.probe_dimension", lambda _self: 2048
    )
    response = client.post(
        f"/api/v1/embedding-profiles/{created.json()['id']}/activate",
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == 422
    assert "configured dimension 1024" in response.text
    assert "provider returned 2048" in response.text
