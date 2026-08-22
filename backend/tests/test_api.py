from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from codeatlas.models import ApiToken, IndexJob, Repository, RepositoryAccess, User
from codeatlas.security import digest_secret
from tests.conftest import login_admin


def test_health_ready_and_no_public_registration(client: TestClient) -> None:
    assert client.get("/api/v1/health").json()["status"] == "ok"
    assert client.get("/api/v1/ready").status_code == 200
    assert client.post("/api/v1/auth/register", json={}).status_code == 404


def test_login_session_csrf_and_logout(client: TestClient, admin: User) -> None:
    invalid = client.post(
        "/api/v1/auth/login",
        json={"email": admin.email, "password": "incorrect password"},
    )
    assert invalid.status_code == 401

    csrf = login_admin(client)
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["role"] == "admin"

    missing_csrf = client.post("/api/v1/auth/logout")
    assert missing_csrf.status_code == 403
    logged_out = client.post(
        "/api/v1/auth/logout", headers={"X-CSRF-Token": csrf}
    )
    assert logged_out.status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401


def test_login_rejects_foreign_origin(client: TestClient, admin: User) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": admin.email, "password": "correct horse battery staple"},
        headers={"Origin": "https://attacker.example"},
    )
    assert response.status_code == 403


def test_admin_repository_member_and_token_workflow(
    client: TestClient,
    application,
    admin: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "codeatlas.api.validate_public_git_url",
        lambda url, _hosts: url,
    )
    csrf = login_admin(client)
    headers = {"X-CSRF-Token": csrf}

    no_csrf = client.post(
        "/api/v1/repositories",
        json={"name": "private-repo", "git_url": "https://github.com/org/repo.git"},
    )
    assert no_csrf.status_code == 403
    created = client.post(
        "/api/v1/repositories",
        headers=headers,
        json={
            "name": "private-repo",
            "description": "Private service",
            "git_url": "https://github.com/org/repo.git",
            "branch": "main",
            "visibility": "private",
        },
    )
    assert created.status_code == 201
    repository_id = created.json()["id"]

    submitted_jobs: list[str] = []
    monkeypatch.setattr(application.state.indexer, "submit", submitted_jobs.append)
    sync = client.post(
        f"/api/v1/repositories/{repository_id}/sync", headers=headers
    )
    assert sync.status_code == 202
    assert sync.json()["status"] == "queued"
    assert submitted_jobs == [sync.json()["id"]]

    member_response = client.post(
        "/api/v1/members",
        headers=headers,
        json={
            "email": "member@example.com",
            "display_name": "Member",
            "password": "member password 1234",
            "role": "member",
        },
    )
    assert member_response.status_code == 201
    member_id = member_response.json()["id"]

    grant = client.put(
        f"/api/v1/members/{member_id}/repositories/{repository_id}", headers=headers
    )
    assert grant.status_code == 204

    token_response = client.post(
        "/api/v1/tokens",
        headers=headers,
        json={
            "name": "MCP local",
            "scopes": ["status", "search", "read"],
            "repository_ids": [repository_id],
        },
    )
    assert token_response.status_code == 201
    raw_token = token_response.json()["token"]
    assert raw_token.startswith("cat_")
    listed = client.get("/api/v1/tokens").json()
    assert "token" not in listed[0]

    with Session(application.state.engine) as session:
        stored_token = session.exec(select(ApiToken)).one()
        repository = session.get(Repository, repository_id)
        assert repository is not None
        repository.status = "ready"
        hidden_repository = Repository(
            name="hidden-repo",
            git_url="https://github.com/org/hidden.git",
            branch="main",
            visibility="private",
            status="ready",
            created_by=admin.id,
        )
        session.add_all([repository, hidden_repository])
        session.flush()
        hidden_job = IndexJob(
            repository_id=hidden_repository.id,
            created_by=admin.id,
            status="succeeded",
        )
        session.add(hidden_job)
        session.commit()
        assert stored_token.token_hash == digest_secret(raw_token)
        assert raw_token not in stored_token.token_hash
        assert session.exec(select(RepositoryAccess)).one().user_id == member_id

    client.post("/api/v1/auth/logout", headers=headers)
    member_login = client.post(
        "/api/v1/auth/login",
        json={"email": "member@example.com", "password": "member password 1234"},
    )
    assert member_login.status_code == 200
    repositories = client.get("/api/v1/repositories").json()
    assert [item["id"] for item in repositories] == [repository_id]
    jobs = client.get("/api/v1/index-jobs")
    assert jobs.status_code == 200
    assert [item["id"] for item in jobs.json()] == [sync.json()["id"]]
    assert client.get("/api/v1/members").status_code == 403


def test_guest_only_sees_ready_public_repositories(
    client: TestClient, application, admin: User
) -> None:
    with Session(application.state.engine) as session:
        session.add_all(
            [
                Repository(
                    name="public-ready",
                    git_url="https://github.com/org/public.git",
                    branch="main",
                    visibility="public",
                    status="ready",
                    created_by=admin.id,
                ),
                Repository(
                    name="private-ready",
                    git_url="https://github.com/org/private.git",
                    branch="main",
                    visibility="private",
                    status="ready",
                    created_by=admin.id,
                ),
                Repository(
                    name="public-pending",
                    git_url="https://github.com/org/pending.git",
                    branch="main",
                    visibility="public",
                    status="pending",
                    created_by=admin.id,
                ),
            ]
        )
        session.commit()
    response = client.get("/api/v1/repositories")
    assert [item["name"] for item in response.json()] == ["public-ready"]


def test_token_rejects_unknown_repository(
    client: TestClient, admin: User
) -> None:
    csrf = login_admin(client)
    response = client.post(
        "/api/v1/tokens",
        headers={"X-CSRF-Token": csrf},
        json={"name": "bad", "repository_ids": ["missing"]},
    )
    assert response.status_code == 422


def test_admin_can_list_pending_repository(
    client: TestClient, application, admin: User
) -> None:
    with Session(application.state.engine) as session:
        pending = Repository(
            name="pending",
            git_url="https://github.com/org/pending.git",
            branch="main",
            visibility="private",
            status="pending",
            created_by=admin.id,
        )
        session.add(pending)
        session.commit()
        session.refresh(pending)
    login_admin(client)
    response = client.get("/api/v1/repositories")
    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [pending.id]


def test_mcp_http_requires_bearer_token(client: TestClient) -> None:
    response = client.post(
        "/mcp/",
        headers={"Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    assert response.status_code == 401
