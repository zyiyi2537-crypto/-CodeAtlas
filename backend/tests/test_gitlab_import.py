from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import login_admin


def test_admin_can_import_gitlab_project_as_repository(
    client: TestClient, admin, monkeypatch
) -> None:
    monkeypatch.setenv("CODEATLAS_CREDENTIAL_GITLAB_PLATFORM_READONLY", "test-token")
    csrf = login_admin(client)
    headers = {"X-CSRF-Token": csrf}

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def project(self, project_id):
            assert project_id == "101"
            return type(
                "Project",
                (),
                {
                    "external_id": "101",
                    "path_with_namespace": "platform/orders",
                    "name": "orders",
                    "description": "Orders service",
                    "default_branch": "main",
                    "web_url": "https://gitlab.example.com/platform/orders",
                    "git_url": "https://gitlab.example.com/platform/orders.git",
                },
            )()

    monkeypatch.setattr("codeatlas.api.GitLabClient", FakeClient)
    source = client.post(
        "/api/v1/gitlab-sources",
        headers=headers,
        json={
            "name": "company-gitlab",
            "base_url": "https://gitlab.example.com",
            "group_path": "platform",
            "credential_ref": "gitlab-platform-readonly",
        },
    )
    assert source.status_code == 201

    imported = client.post(
        f"/api/v1/gitlab-sources/{source.json()['id']}/import",
        headers=headers,
        json={"external_project_id": "101", "visibility": "private"},
    )
    assert imported.status_code == 201
    assert imported.json()["name"] == "orders"
    assert imported.json()["git_url"].endswith("orders.git")

    duplicate = client.post(
        f"/api/v1/gitlab-sources/{source.json()['id']}/import",
        headers=headers,
        json={"external_project_id": "101", "visibility": "private"},
    )
    assert duplicate.status_code == 409
