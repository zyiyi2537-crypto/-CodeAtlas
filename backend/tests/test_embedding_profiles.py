from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from codeatlas.embeddings import resolve_embedding_api_key
from codeatlas.models import EmbeddingProfile, IndexJob, Repository
from codeatlas.vector_store import VectorStore, code_generation_namespace
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


def test_browser_managed_embedding_key_does_not_require_server_reference(
    client: TestClient, application, admin, monkeypatch
) -> None:
    csrf = login_admin(client)
    monkeypatch.setattr(
        "codeatlas.api.EmbeddingClient.probe_dimension", lambda _self: 1024
    )
    probe = client.post(
        "/api/v1/embedding-profiles/probe",
        headers={"X-CSRF-Token": csrf},
        json={
            "base_url": "https://embedding.example/v1",
            "model": "bge-m3",
            "provider": "openai",
            "api_key": "browser-only-key",
        },
    )
    assert probe.status_code == 200
    assert probe.json() == {"dimension": 1024}
    assert "browser-only-key" not in probe.text

    response = client.post(
        "/api/v1/embedding-profiles",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "browser-only-bge",
            "base_url": "https://embedding.example/v1",
            "model": "bge-m3",
            "dimension": 1024,
            "api_key": "browser-only-key",
        },
    )

    assert response.status_code == 201
    assert response.json()["credential_configured"] is True
    assert "browser-only-key" not in response.text
    with Session(application.state.engine) as session:
        profile = session.get(EmbeddingProfile, response.json()["id"])
        assert profile is not None
        assert profile.credential_ref.startswith("embedding-")
        assert resolve_embedding_api_key(
            profile, application.state.settings.data_dir
        ) == "browser-only-key"


def test_embedding_profile_encrypts_replaces_keeps_and_clears_browser_key(
    client: TestClient, application, admin
) -> None:
    csrf = login_admin(client)
    first_key = "browser-managed-embedding-key-one"
    second_key = "browser-managed-embedding-key-two"
    created = client.post(
        "/api/v1/embedding-profiles",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "browser-bge",
            "base_url": "https://embedding.example/v1",
            "model": "bge-m3",
            "dimension": 1024,
            "credential_ref": "browser-bge",
            "api_key": first_key,
        },
    )
    assert created.status_code == 201
    profile_id = created.json()["id"]
    assert created.json()["credential_configured"] is True
    assert "api_key" not in created.text
    assert first_key not in created.text

    with Session(application.state.engine) as session:
        profile = session.get(EmbeddingProfile, profile_id)
        assert profile is not None
        assert profile.api_key_ciphertext
        assert first_key not in profile.api_key_ciphertext
        assert resolve_embedding_api_key(
            profile,
            application.state.settings.data_dir,
        ) == first_key

    kept = client.patch(
        f"/api/v1/embedding-profiles/{profile_id}",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "browser-bge-updated",
            "base_url": "https://embedding.example/v1",
            "model": "bge-m3-v2",
            "dimension": 1024,
            "provider": "openai",
            "api_key": "",
            "clear_api_key": False,
        },
    )
    assert kept.status_code == 200
    with Session(application.state.engine) as session:
        profile = session.get(EmbeddingProfile, profile_id)
        assert profile is not None
        assert resolve_embedding_api_key(
            profile,
            application.state.settings.data_dir,
        ) == first_key

    replaced = client.patch(
        f"/api/v1/embedding-profiles/{profile_id}",
        headers={"X-CSRF-Token": csrf},
        json={"api_key": second_key, "clear_api_key": False},
    )
    assert replaced.status_code == 200
    assert second_key not in replaced.text
    with Session(application.state.engine) as session:
        profile = session.get(EmbeddingProfile, profile_id)
        assert profile is not None
        assert resolve_embedding_api_key(
            profile,
            application.state.settings.data_dir,
        ) == second_key

    cleared = client.patch(
        f"/api/v1/embedding-profiles/{profile_id}",
        headers={"X-CSRF-Token": csrf},
        json={"api_key": "", "clear_api_key": True},
    )
    assert cleared.status_code == 200
    assert cleared.json()["credential_configured"] is False
    with Session(application.state.engine) as session:
        profile = session.get(EmbeddingProfile, profile_id)
        assert profile is not None
        assert profile.api_key_ciphertext == ""


def test_embedding_profile_browser_key_falls_back_to_server_reference(
    client: TestClient, application, admin, monkeypatch
) -> None:
    monkeypatch.setenv("CODEATLAS_CREDENTIAL_FALLBACK_BGE", "server-fallback-key")
    csrf = login_admin(client)
    created = client.post(
        "/api/v1/embedding-profiles",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "fallback-bge",
            "base_url": "https://embedding.example/v1",
            "model": "bge-m3",
            "dimension": 1024,
            "credential_ref": "fallback-bge",
            "api_key": "browser-override-key",
        },
    )
    profile_id = created.json()["id"]
    cleared = client.patch(
        f"/api/v1/embedding-profiles/{profile_id}",
        headers={"X-CSRF-Token": csrf},
        json={"clear_api_key": True},
    )
    assert cleared.status_code == 200
    assert cleared.json()["credential_configured"] is True
    with Session(application.state.engine) as session:
        profile = session.get(EmbeddingProfile, profile_id)
        assert profile is not None
        assert resolve_embedding_api_key(
            profile,
            application.state.settings.data_dir,
        ) == "server-fallback-key"


def test_saved_embedding_key_is_not_sent_to_an_unsaved_base_url(
    client: TestClient, admin, monkeypatch
) -> None:
    csrf = login_admin(client)
    created = client.post(
        "/api/v1/embedding-profiles",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "host-bound-bge",
            "base_url": "https://embedding.example/v1",
            "model": "bge-m3",
            "dimension": 1024,
            "credential_ref": "host-bound-bge",
            "api_key": "host-bound-browser-key",
        },
    )
    called = False

    class ProbeClient:
        def __init__(self, _settings):
            nonlocal called
            called = True

        def probe_dimension(self):
            return 1024

    monkeypatch.setattr("codeatlas.api.EmbeddingClient", ProbeClient)
    response = client.post(
        "/api/v1/embedding-profiles/probe",
        headers={"X-CSRF-Token": csrf},
        json={
            "profile_id": created.json()["id"],
            "base_url": "https://untrusted.example/v1",
            "model": "bge-m3",
            "provider": "openai",
            "api_key": "",
        },
    )

    assert response.status_code == 422
    assert called is False
    assert "replacement API key" in response.text

    saved = client.patch(
        f"/api/v1/embedding-profiles/{created.json()['id']}",
        headers={"X-CSRF-Token": csrf},
        json={"base_url": "https://untrusted.example/v1", "api_key": ""},
    )
    assert saved.status_code == 422
    profiles = client.get("/api/v1/embedding-profiles").json()
    assert profiles[0]["base_url"] == "https://embedding.example/v1"


def test_active_embedding_profile_rejects_vector_edits_clear_and_delete(
    client: TestClient, application, admin, monkeypatch
) -> None:
    csrf = login_admin(client)
    created = client.post(
        "/api/v1/embedding-profiles",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "protected-bge",
            "base_url": "https://embedding.example/v1",
            "model": "bge-m3",
            "dimension": 128,
            "credential_ref": "protected-bge",
            "api_key": "protected-browser-key",
        },
    )
    monkeypatch.setattr(
        "codeatlas.api.EmbeddingClient.probe_dimension", lambda _self: 128
    )
    activated = client.post(
        f"/api/v1/embedding-profiles/{created.json()['id']}/activate",
        headers={"X-CSRF-Token": csrf},
    )
    assert activated.status_code == 200
    profile_id = created.json()["id"]

    vector_edit = client.patch(
        f"/api/v1/embedding-profiles/{profile_id}",
        headers={"X-CSRF-Token": csrf},
        json={"model": "different-model"},
    )
    assert vector_edit.status_code == 409
    clear = client.patch(
        f"/api/v1/embedding-profiles/{profile_id}",
        headers={"X-CSRF-Token": csrf},
        json={"clear_api_key": True},
    )
    assert clear.status_code == 409
    delete = client.delete(
        f"/api/v1/embedding-profiles/{profile_id}",
        headers={"X-CSRF-Token": csrf},
    )
    assert delete.status_code == 409


def test_inactive_embedding_profile_deletes_all_profile_collections(
    client: TestClient, application, admin
) -> None:
    csrf = login_admin(client)
    created = client.post(
        "/api/v1/embedding-profiles",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "delete-bge",
            "base_url": "https://embedding.example/v1",
            "model": "bge-m3",
            "dimension": 128,
            "credential_ref": "delete-bge",
            "api_key": "delete-browser-key",
        },
    )
    profile_id = created.json()["id"]
    store = VectorStore(application.state.settings, namespace=profile_id)
    orphan_namespace = code_generation_namespace(profile_id, "orphan-generation")
    VectorStore(application.state.settings, namespace=orphan_namespace)
    assert store.has_namespace(profile_id)
    assert store.has_namespace(orphan_namespace)

    deleted = client.delete(
        f"/api/v1/embedding-profiles/{profile_id}",
        headers={"X-CSRF-Token": csrf},
    )
    assert deleted.status_code == 204
    assert client.get("/api/v1/embedding-profiles").json() == []
    assert not store.has_namespace(profile_id)
    assert not store.has_namespace(orphan_namespace)


def test_embedding_profile_with_searchable_generation_cannot_be_deleted(
    client: TestClient, application, admin
) -> None:
    csrf = login_admin(client)
    created = client.post(
        "/api/v1/embedding-profiles",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "serving-bge",
            "base_url": "https://embedding.example/v1",
            "model": "bge-m3",
            "dimension": 128,
            "credential_ref": "serving-bge",
            "api_key": "serving-browser-key",
        },
    )
    profile_id = created.json()["id"]
    generation_id = "serving-generation"
    with Session(application.state.engine) as session:
        session.add(
            Repository(
                name="serving-profile-repository",
                git_url="https://github.com/example/serving-profile-repository.git",
                status="ready",
                active_generation_id=generation_id,
                created_by=admin.id,
            )
        )
        session.commit()
    namespace = code_generation_namespace(profile_id, generation_id)
    store = VectorStore(application.state.settings, namespace=namespace)

    deleted = client.delete(
        f"/api/v1/embedding-profiles/{profile_id}",
        headers={"X-CSRF-Token": csrf},
    )

    assert deleted.status_code == 409
    assert store.has_namespace(namespace)


def test_editing_inactive_embedding_vector_settings_clears_old_collections(
    client: TestClient, application, admin
) -> None:
    csrf = login_admin(client)
    created = client.post(
        "/api/v1/embedding-profiles",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "stale-bge",
            "base_url": "https://embedding.example/v1",
            "model": "old-model",
            "dimension": 128,
            "credential_ref": "stale-bge",
            "api_key": "old-model-key",
        },
    )
    profile_id = created.json()["id"]
    profile_store = VectorStore(application.state.settings, namespace=profile_id)
    stale_namespace = code_generation_namespace(profile_id, "stale-generation")
    VectorStore(application.state.settings, namespace=stale_namespace)
    assert profile_store.has_namespace(profile_id)
    assert profile_store.has_namespace(stale_namespace)

    updated = client.patch(
        f"/api/v1/embedding-profiles/{profile_id}",
        headers={"X-CSRF-Token": csrf},
        json={
            "model": "new-model",
            "api_key": "new-model-key",
        },
    )

    assert updated.status_code == 200
    assert updated.json()["model"] == "new-model"
    assert not profile_store.has_namespace(profile_id)
    assert not profile_store.has_namespace(stale_namespace)


def test_invalid_embedding_edit_does_not_delete_old_collections(
    client: TestClient, application, admin
) -> None:
    csrf = login_admin(client)
    created = client.post(
        "/api/v1/embedding-profiles",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "invalid-edit-bge",
            "base_url": "https://embedding.example/v1",
            "model": "old-model",
            "dimension": 128,
            "credential_ref": "invalid-edit-bge",
            "api_key": "old-model-key",
        },
    )
    profile_id = created.json()["id"]
    profile_store = VectorStore(application.state.settings, namespace=profile_id)
    stale_namespace = code_generation_namespace(profile_id, "stale-generation")
    VectorStore(application.state.settings, namespace=stale_namespace)

    response = client.patch(
        f"/api/v1/embedding-profiles/{profile_id}",
        headers={"X-CSRF-Token": csrf},
        json={
            "model": "new-model",
            "credential_ref": "invalid reference with spaces",
            "api_key": "new-model-key",
        },
    )

    assert response.status_code == 422
    assert profile_store.has_namespace(profile_id)
    assert profile_store.has_namespace(stale_namespace)


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


def test_embedding_rebuild_failure_still_submits_repository_jobs(
    client: TestClient, application, admin, monkeypatch
) -> None:
    monkeypatch.setenv("CODEATLAS_CREDENTIAL_EMBEDDING_COMPANY", "server-only-key")
    with Session(application.state.engine) as session:
        repository = Repository(
            name="rebuild-failure-repository",
            git_url="https://github.com/example/rebuild-failure.git",
            branch="main",
            visibility="public",
            status="ready",
            last_commit="b" * 40,
            created_by=admin.id,
        )
        session.add(repository)
        session.commit()

    csrf = login_admin(client)
    created = client.post(
        "/api/v1/embedding-profiles",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "rebuild-failure-bge",
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
    monkeypatch.setattr(
        application.state.knowledge_search,
        "rebuild_all",
        lambda: (_ for _ in ()).throw(RuntimeError("knowledge rebuild failed")),
    )

    response = client.post(
        f"/api/v1/embedding-profiles/{created.json()['id']}/activate",
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == 502
    assert "already active" in response.text
    assert len(submitted) == 1
    assert application.state.external_sync.embedding_switch_in_progress is False
    profiles = client.get("/api/v1/embedding-profiles").json()
    assert next(item for item in profiles if item["id"] == created.json()["id"])[
        "is_active"
    ] is True


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
