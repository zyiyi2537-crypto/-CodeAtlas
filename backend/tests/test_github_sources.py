from __future__ import annotations

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi.testclient import TestClient

from codeatlas.github import GitHubBranchNotFoundError
from tests.conftest import login_admin


def test_admin_can_generate_key_and_create_github_source(
    client: TestClient, admin, monkeypatch
) -> None:
    monkeypatch.setattr("codeatlas.api.remote_commit", lambda *_args, **_kwargs: "a" * 40)
    csrf = login_admin(client)
    headers = {"X-CSRF-Token": csrf}

    key = client.post("/api/v1/github-keys", headers=headers)
    assert key.status_code == 201
    assert key.json()["public_key"].startswith("ssh-ed25519 ")
    fields = key.json()["public_key"].split()
    assert len(fields) == 3
    assert "\n" not in key.json()["public_key"]
    decoded = base64.b64decode(fields[1], validate=True)
    Ed25519PublicKey.from_public_bytes(decoded[-32:]).public_bytes(
        serialization.Encoding.OpenSSH,
        serialization.PublicFormat.OpenSSH,
    )
    key_id = key.json()["key_id"]
    assert key_id in key.json()["public_key"]

    source = client.post(
        "/api/v1/github-sources",
        headers=headers,
        json={
            "name": "hello-world",
            "repo_url": "git@github.com:octocat/Hello-World.git",
            "branch": "main",
            "ssh_key_id": key_id,
            "visibility": "private",
        },
    )
    assert source.status_code == 201
    payload = source.json()
    assert payload["repo_url"] == "git@github.com:octocat/Hello-World.git"
    assert payload["deploy_key_configured"] is True


def test_github_source_rejects_https_clone_url(client: TestClient, admin) -> None:
    csrf = login_admin(client)
    key = client.post("/api/v1/github-keys", headers={"X-CSRF-Token": csrf}).json()
    response = client.post(
        "/api/v1/github-sources",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "https-source",
            "repo_url": "https://github.com/octocat/Hello-World.git",
            "ssh_key_id": key["key_id"],
            "visibility": "private",
        },
    )
    assert response.status_code == 422


def test_public_github_source_accepts_https_without_deploy_key(
    client: TestClient, admin, monkeypatch
) -> None:
    monkeypatch.setenv("CODEATLAS_ALLOW_PRIVATE_GIT_HOSTS", "true")
    monkeypatch.setattr(
        "codeatlas.api.remote_commit",
        lambda _settings, _url, branch, _key="": (
            "a" * 40 if branch == "master" else ""
        ),
    )
    csrf = login_admin(client)
    response = client.post(
        "/api/v1/github-sources",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "yt-dlp-public",
            "repo_url": "https://github.com/yt-dlp/yt-dlp.git",
            "branch": "master",
            "visibility": "public",
            "include_paths": ["templates/website/src/Header/", "templates/website/src/blocks"],
        },
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["repo_url"] == "https://github.com/yt-dlp/yt-dlp.git"
    assert payload["branch"] == "master"
    assert payload["deploy_key_configured"] is False
    assert payload["include_paths"] == [
        "templates/website/src/Header",
        "templates/website/src/blocks",
    ]
    assert (
        client.get("/api/v1/github-sources").json()[0]["include_paths"]
        == payload["include_paths"]
    )


def test_github_source_rejects_unsafe_include_paths(client: TestClient, admin) -> None:
    csrf = login_admin(client)
    response = client.post(
        "/api/v1/github-sources",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "unsafe-scope",
            "repo_url": "https://github.com/yt-dlp/yt-dlp.git",
            "branch": "master",
            "visibility": "public",
            "include_paths": ["../outside"],
        },
    )

    assert response.status_code == 422


def test_public_github_source_accepts_pinned_commit_and_license(
    client: TestClient, admin, monkeypatch
) -> None:
    commit = "b" * 40
    monkeypatch.setattr("codeatlas.api.validate_public_commit", lambda *_args: commit)
    csrf = login_admin(client)
    response = client.post(
        "/api/v1/github-sources",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "pinned-source",
            "repo_url": "https://github.com/yt-dlp/yt-dlp.git",
            "branch": "master",
            "visibility": "public",
            "pinned_commit": commit,
            "license_name": "MIT",
            "license_url": "https://github.com/yt-dlp/yt-dlp/blob/main/LICENSE",
            "include_paths": ["templates/website"],
        },
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["pinned_commit"] == commit
    assert payload["license_name"] == "MIT"
    assert payload["license_url"].endswith("/LICENSE")


def test_github_source_rejects_missing_branch_before_saving(
    client: TestClient, admin, monkeypatch
) -> None:
    monkeypatch.setenv("CODEATLAS_ALLOW_PRIVATE_GIT_HOSTS", "true")
    monkeypatch.setattr(
        "codeatlas.api.remote_commit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            GitHubBranchNotFoundError("GitHub branch does not exist: main")
        ),
    )
    csrf = login_admin(client)

    response = client.post(
        "/api/v1/github-sources",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "invalid-branch",
            "repo_url": "https://github.com/yt-dlp/yt-dlp.git",
            "branch": "main",
            "visibility": "public",
        },
    )

    assert response.status_code == 422
    assert "branch" in response.json()["detail"].lower()
    assert client.get("/api/v1/github-sources").json() == []


def test_private_github_source_requires_deploy_key(client: TestClient, admin) -> None:
    csrf = login_admin(client)
    response = client.post(
        "/api/v1/github-sources",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": "private-no-key",
            "repo_url": "git@github.com:octocat/private.git",
            "branch": "main",
            "visibility": "private",
        },
    )

    assert response.status_code == 422
    assert "Deploy Key" in response.json()["detail"]
