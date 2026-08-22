from __future__ import annotations

from dataclasses import dataclass

from fastapi.testclient import TestClient

from tests.conftest import login_admin


@dataclass(frozen=True)
class FakeProject:
    external_id: str = "101"
    path_with_namespace: str = "platform/orders"
    name: str = "orders"
    description: str = "Orders service"
    default_branch: str = "main"
    web_url: str = "https://gitlab.example.com/platform/orders"
    git_url: str = "https://gitlab.example.com/platform/orders.git"


class FakeGitLabClient:
    def __init__(self, base_url: str, token: str):
        assert base_url == "https://gitlab.example.com"
        assert token == "test-token"

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def list_group_projects(self, group: str):
        assert group == "platform"
        return [FakeProject()]


def test_admin_can_create_and_discover_gitlab_source(
    client: TestClient, admin, monkeypatch
) -> None:
    monkeypatch.setenv("CODEATLAS_CREDENTIAL_GITLAB_PLATFORM_READONLY", "test-token")
    monkeypatch.setattr("codeatlas.api.GitLabClient", FakeGitLabClient)
    csrf = login_admin(client)
    headers = {"X-CSRF-Token": csrf}

    created = client.post(
        "/api/v1/gitlab-sources",
        headers=headers,
        json={
            "name": "company-gitlab",
            "base_url": "https://gitlab.example.com/",
            "group_path": "platform",
            "credential_ref": "gitlab-platform-readonly",
        },
    )
    assert created.status_code == 201
    payload = created.json()
    assert payload["credential_ref"] == "gitlab-platform-readonly"
    assert "token" not in payload

    sources = client.get("/api/v1/gitlab-sources")
    assert sources.status_code == 200
    source_id = sources.json()[0]["id"]

    projects = client.get(f"/api/v1/gitlab-sources/{source_id}/projects")
    assert projects.status_code == 200
    assert projects.json()[0]["path_with_namespace"] == "platform/orders"


def test_gitlab_source_requires_configured_credential(client: TestClient, admin) -> None:
    csrf = login_admin(client)
    response = client.post(
        "/api/v1/gitlab-sources",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "missing-credential",
            "base_url": "https://gitlab.example.com",
            "group_path": "platform",
            "credential_ref": "missing-ref",
        },
    )
    assert response.status_code == 503
