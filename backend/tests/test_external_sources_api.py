from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from codeatlas.database import initialize_database
from codeatlas.models import DocumentCollection, ExternalSource
from tests.conftest import login_admin


def _collection(client: TestClient, csrf: str) -> str:
    response = client.post(
        "/api/v1/document-collections",
        headers={"X-CSRF-Token": csrf},
        json={"name": "External documents"},
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def test_admin_can_create_list_test_and_sync_object_storage_source(
    client: TestClient, application, admin, monkeypatch
) -> None:
    csrf = login_admin(client)
    collection_id = _collection(client, csrf)
    tested: list[str] = []
    submitted: list[str] = []

    monkeypatch.setattr(
        application.state.external_sync,
        "test_source",
        lambda source_id: tested.append(source_id),
    )
    monkeypatch.setattr(
        application.state.external_sync,
        "submit",
        lambda source_id: submitted.append(source_id),
    )
    response = client.post(
        "/api/v1/external-sources",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "Product manuals",
            "provider": "aws_s3",
            "collection_id": collection_id,
            "credential_ref": "aws-docs",
            "poll_interval_seconds": 1800,
            "config": {
                "bucket": "company-docs",
                "prefix": "manuals/",
                "region": "ap-southeast-1",
            },
        },
    )

    assert response.status_code == 201, response.text
    source = response.json()
    assert source["provider"] == "aws_s3"
    assert source["credential_ref"] == "已配置"
    assert source["credential_env"] == "CODEATLAS_CREDENTIAL_AWS_DOCS"
    assert source["config"]["bucket"] == "company-docs"
    assert "secret" not in json.dumps(source).lower()

    listed = client.get("/api/v1/external-sources")
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == source["id"]

    tested_response = client.post(
        f"/api/v1/external-sources/{source['id']}/test",
        headers={"X-CSRF-Token": csrf},
    )
    assert tested_response.status_code == 200
    assert tested == [source["id"]]

    sync_response = client.post(
        f"/api/v1/external-sources/{source['id']}/sync",
        headers={"X-CSRF-Token": csrf},
    )
    assert sync_response.status_code == 202
    assert submitted == [source["id"]]


def test_external_source_rejects_secret_like_credential_and_invalid_provider(
    client: TestClient, application, admin
) -> None:
    csrf = login_admin(client)
    collection_id = _collection(client, csrf)
    headers = {"X-CSRF-Token": csrf}
    base = {
        "name": "Bad source",
        "collection_id": collection_id,
        "credential_ref": "sk-this-is-a-real-looking-secret-value",
        "config": {"bucket": "docs", "region": "ap-southeast-1"},
    }

    secret = client.post(
        "/api/v1/external-sources",
        headers=headers,
        json={**base, "provider": "aws_s3"},
    )
    assert secret.status_code == 422

    invalid = client.post(
        "/api/v1/external-sources",
        headers=headers,
        json={**base, "credential_ref": "safe-ref", "provider": "dropbox"},
    )
    assert invalid.status_code == 422
    with Session(application.state.engine) as session:
        assert session.exec(select(ExternalSource)).all() == []
        assert len(session.exec(select(DocumentCollection)).all()) == 1


def test_object_storage_source_validates_required_config(
    client: TestClient, admin
) -> None:
    csrf = login_admin(client)
    collection_id = _collection(client, csrf)
    response = client.post(
        "/api/v1/external-sources",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "Missing bucket",
            "provider": "tencent_cos",
            "collection_id": collection_id,
            "credential_ref": "cos-docs",
            "config": {"region": "ap-shanghai"},
        },
    )
    assert response.status_code == 422
    assert "bucket" in response.json()["detail"].lower()


def test_admin_can_create_notion_and_confluence_sources(
    client: TestClient, admin, monkeypatch
) -> None:
    monkeypatch.setenv("CODEATLAS_ALLOWED_EXTERNAL_HOSTS", "company.atlassian.net")
    csrf = login_admin(client)
    collection_id = _collection(client, csrf)
    headers = {"X-CSRF-Token": csrf}

    notion = client.post(
        "/api/v1/external-sources",
        headers=headers,
        json={
            "name": "Engineering Notion",
            "provider": "notion",
            "collection_id": collection_id,
            "credential_ref": "notion-engineering",
            "config": {"root_page_id": "page-root"},
        },
    )
    confluence = client.post(
        "/api/v1/external-sources",
        headers=headers,
        json={
            "name": "Engineering Confluence",
            "provider": "confluence",
            "collection_id": collection_id,
            "credential_ref": "confluence-engineering",
            "config": {
                "base_url": "https://company.atlassian.net/wiki",
                "space_key": "ENG",
                "deployment": "cloud",
            },
        },
    )

    assert notion.status_code == 201, notion.text
    assert confluence.status_code == 201, confluence.text


def test_admin_can_delete_external_source(
    client: TestClient, application, admin, monkeypatch
) -> None:
    csrf = login_admin(client)
    collection_id = _collection(client, csrf)
    headers = {"X-CSRF-Token": csrf}
    created = client.post(
        "/api/v1/external-sources",
        headers=headers,
        json={
            "name": "Delete me",
            "provider": "aws_s3",
            "collection_id": collection_id,
            "credential_ref": "aws-docs",
            "config": {"bucket": "company-docs", "region": "ap-southeast-1"},
        },
    )
    source_id = created.json()["id"]
    deleted: list[str] = []
    monkeypatch.setattr(
        application.state.external_sync,
        "delete_source",
        lambda value: deleted.append(value),
    )

    response = client.delete(
        f"/api/v1/external-sources/{source_id}", headers=headers
    )

    assert response.status_code == 204
    assert deleted == [source_id]


def test_external_source_rejects_private_confluence_url(client: TestClient, admin) -> None:
    csrf = login_admin(client)
    collection_id = _collection(client, csrf)
    response = client.post(
        "/api/v1/external-sources",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "Internal Confluence",
            "provider": "confluence",
            "collection_id": collection_id,
            "credential_ref": "confluence-internal",
            "config": {
                "base_url": "https://127.0.0.1/wiki",
                "space_key": "ENG",
                "deployment": "data_center",
            },
        },
    )
    assert response.status_code == 422


def test_external_source_rejects_private_s3_endpoint(client: TestClient, admin) -> None:
    csrf = login_admin(client)
    collection_id = _collection(client, csrf)
    response = client.post(
        "/api/v1/external-sources",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "Internal S3",
            "provider": "aws_s3",
            "collection_id": collection_id,
            "credential_ref": "aws-internal",
            "config": {
                "bucket": "docs",
                "region": "us-east-1",
                "endpoint_url": "https://127.0.0.1:9000",
            },
        },
    )
    assert response.status_code == 422


def test_external_sync_status_recovers_after_service_restart(application, admin) -> None:
    with Session(application.state.engine) as session:
        collection = DocumentCollection(
            name="Recovery documents", description="", created_by=admin.id
        )
        session.add(collection)
        session.flush()
        source = ExternalSource(
            name="Recovery source",
            provider="notion",
            collection_id=collection.id,
            credential_ref="notion-recovery",
            config_json="{}",
            sync_status="syncing",
            created_by=admin.id,
        )
        session.add(source)
        session.commit()
        source_id = source.id

    initialize_database(application.state.settings, application.state.engine)

    with Session(application.state.engine) as session:
        recovered = session.get(ExternalSource, source_id)
        assert recovered is not None and recovered.sync_status == "queued"
        assert recovered.last_error == "Recovered after service restart"


def test_external_source_connection_error_is_redacted(
    client: TestClient, application, admin, monkeypatch
) -> None:
    csrf = login_admin(client)
    collection_id = _collection(client, csrf)
    created = client.post(
        "/api/v1/external-sources",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "Connection failure",
            "provider": "aws_s3",
            "collection_id": collection_id,
            "credential_ref": "aws-docs",
            "config": {"bucket": "docs", "region": "us-east-1"},
        },
    )
    monkeypatch.setattr(
        application.state.external_sync,
        "test_source",
        lambda _source_id: (_ for _ in ()).throw(
            OSError("token=secret-value-that-must-not-leak")
        ),
    )

    response = client.post(
        f"/api/v1/external-sources/{created.json()['id']}/test",
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == 422
    assert "secret-value" not in response.text
    assert "[REDACTED]" in response.text


def test_external_source_dns_failure_returns_validation_error(
    client: TestClient, admin, monkeypatch
) -> None:
    import socket

    original_getaddrinfo = socket.getaddrinfo

    def fail_target_dns(host, *args, **kwargs):
        if host == "confluence.example.invalid":
            raise OSError("DNS unavailable")
        return original_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(
        "codeatlas.connectors.socket.getaddrinfo",
        fail_target_dns,
    )
    csrf = login_admin(client)
    collection_id = _collection(client, csrf)

    response = client.post(
        "/api/v1/external-sources",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "DNS failure",
            "provider": "confluence",
            "collection_id": collection_id,
            "credential_ref": "confluence-dns",
            "config": {
                "base_url": "https://confluence.example.invalid/wiki",
                "space_key": "ENG",
                "deployment": "cloud",
            },
        },
    )

    assert response.status_code == 422
    assert "DNS" in response.json()["detail"]
