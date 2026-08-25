from __future__ import annotations

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi.testclient import TestClient

from tests.conftest import login_admin


def test_admin_can_generate_key_and_create_github_source(
    client: TestClient, admin
) -> None:
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
            "visibility": "public",
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
        },
    )
    assert response.status_code == 422
