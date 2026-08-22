from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import login_admin


def test_admin_can_upload_markdown_document(
    client: TestClient, admin, tmp_path: Path
) -> None:
    csrf = login_admin(client)
    headers = {"X-CSRF-Token": csrf}
    collection = client.post(
        "/api/v1/document-collections",
        headers=headers,
        json={"name": "订单文档", "description": "订单项目资料"},
    )
    assert collection.status_code == 201
    collection_id = collection.json()["id"]

    uploaded = client.post(
        f"/api/v1/document-collections/{collection_id}/documents",
        headers=headers,
        files={
            "file": (
                "refund-standard.md",
                b"# Refund\n\n## Idempotency\nRefunds must be idempotent.",
                "text/markdown",
            )
        },
        data={"title": "退款规范"},
    )
    assert uploaded.status_code == 201
    payload = uploaded.json()
    assert payload["title"] == "退款规范"
    assert payload["status"] == "indexed"
    assert payload["version"] == 1

    search = client.post(
        "/api/v1/documents/search",
        headers=headers,
        json={"query": "退款 幂等", "collection_ids": [collection_id]},
    )
    assert search.status_code == 200
    assert search.json()[0]["source_type"] == "document"
    assert "idempotent" in search.json()[0]["content"]


def test_document_upload_rejects_unsupported_type(client: TestClient, admin) -> None:
    csrf = login_admin(client)
    headers = {"X-CSRF-Token": csrf}
    collection = client.post(
        "/api/v1/document-collections",
        headers=headers,
        json={"name": "资料"},
    )
    response = client.post(
        f"/api/v1/document-collections/{collection.json()['id']}/documents",
        headers=headers,
        files={"file": ("bad.exe", b"MZ", "application/octet-stream")},
    )
    assert response.status_code == 415
