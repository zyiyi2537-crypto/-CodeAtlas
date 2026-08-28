from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from codeatlas.api import DUMMY_PASSWORD_HASH
from codeatlas.models import ApiToken, IndexJob, Repository, RepositoryAccess, User
from codeatlas.security import digest_secret
from tests.conftest import login_admin


def test_login_rate_limit_is_atomic_under_concurrency() -> None:
    from codeatlas.api import check_login_rate_limit, login_attempts

    identifier = "concurrent-login@example.com"

    def attempt() -> int:
        try:
            check_login_rate_limit(identifier)
            return 200
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=20) as executor:
        results = list(executor.map(lambda _index: attempt(), range(20)))

    assert results.count(200) == 5
    assert results.count(429) == 15
    assert len(login_attempts[identifier]) == 5


def test_login_rate_limit_caps_unique_identifiers(monkeypatch) -> None:
    from codeatlas.api import check_login_rate_limit, login_attempts

    monkeypatch.setattr("codeatlas.api.MAX_LOGIN_IDENTIFIERS", 8)
    for index in range(20):
        check_login_rate_limit(f"identifier-{index}")

    assert len(login_attempts) == 8
    assert "identifier-19" in login_attempts


def test_login_rejects_oversized_email_without_echoing_it(client: TestClient) -> None:
    oversized = "x" * 321

    response = client.post(
        "/api/v1/auth/login",
        json={"email": oversized, "password": "password"},
    )

    assert response.status_code == 422
    assert oversized not in response.text


def test_successful_login_clears_rate_limit_entry(client: TestClient, admin: User) -> None:
    from codeatlas.api import login_attempts, login_ip_attempts

    response = client.post(
        "/api/v1/auth/login",
        json={"email": admin.email, "password": "correct horse battery staple"},
    )

    assert response.status_code == 200
    assert login_attempts == {}
    assert len(login_ip_attempts) == 1
    assert all(len(identifier) == 64 for identifier in login_ip_attempts)


def test_login_ip_limit_aggregates_distinct_accounts() -> None:
    from codeatlas.api import LOGIN_IP_LIMIT, check_login_rate_limits

    ip_identifier = digest_secret("ip:192.0.2.10")
    for index in range(LOGIN_IP_LIMIT):
        check_login_rate_limits(
            digest_secret(f"account:user-{index}@example.com"),
            ip_identifier,
        )

    with pytest.raises(HTTPException) as exc_info:
        check_login_rate_limits(
            digest_secret("account:blocked@example.com"),
            ip_identifier,
        )

    assert exc_info.value.status_code == 429


def test_login_account_limit_cannot_be_bypassed_by_rotating_ip() -> None:
    from codeatlas.api import LOGIN_LIMIT, check_login_rate_limits

    account_identifier = digest_secret("account:target@example.com")
    for index in range(LOGIN_LIMIT):
        check_login_rate_limits(
            account_identifier,
            digest_secret(f"ip:192.0.2.{index + 20}"),
        )

    with pytest.raises(HTTPException) as exc_info:
        check_login_rate_limits(
            account_identifier,
            digest_secret("ip:198.51.100.20"),
        )

    assert exc_info.value.status_code == 429


def test_login_verification_slots_limit_same_ip_and_global_concurrency() -> None:
    from codeatlas.api import (
        active_login_ips,
        login_verification_slot,
    )

    first_ip = digest_secret("ip:192.0.2.11")
    second_ip = digest_secret("ip:192.0.2.12")
    third_ip = digest_secret("ip:192.0.2.13")

    with login_verification_slot(first_ip):
        with pytest.raises(HTTPException) as same_ip_error:
            with login_verification_slot(first_ip):
                pass
        assert same_ip_error.value.status_code == 429

        with login_verification_slot(second_ip):
            with pytest.raises(HTTPException) as global_error:
                with login_verification_slot(third_ip):
                    pass
            assert global_error.value.status_code == 429

    assert active_login_ips == set()


def test_login_always_performs_one_password_verification(
    client: TestClient, application, admin: User, monkeypatch
) -> None:
    calls: list[str] = []

    def fake_verify(_password: str, password_hash: str) -> bool:
        calls.append(password_hash)
        return False

    monkeypatch.setattr("codeatlas.api.verify_password", fake_verify)

    unknown = client.post(
        "/api/v1/auth/login",
        json={"email": "missing@example.com", "password": "incorrect password"},
    )
    assert unknown.status_code == 401
    assert calls == [DUMMY_PASSWORD_HASH]

    calls.clear()
    with Session(application.state.engine) as session:
        stored = session.get(User, admin.id)
        assert stored is not None
        stored.is_active = False
        session.add(stored)
        session.commit()
    disabled = client.post(
        "/api/v1/auth/login",
        json={"email": admin.email, "password": "incorrect password"},
    )
    assert disabled.status_code == 401
    assert calls == [admin.password_hash]


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
