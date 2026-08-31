from __future__ import annotations

import socket
from pathlib import Path

import pytest

from codeatlas.security import (
    contains_secret,
    digest_secret,
    redact_secrets,
    resolve_repository_file,
    validate_git_branch,
    validate_public_git_url,
)


def public_dns(*_args, **_kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("140.82.112.3", 443))]


def test_public_git_url_accepts_allowlisted_https(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", public_dns)
    url = "https://github.com/pallets/itsdangerous.git"
    assert validate_public_git_url(url, ("github.com",)) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/org/repo.git",
        "https://user:secret@github.com/org/repo.git",
        "https://github.com/org/repo",
        "https://github.com/org/repo.git?token=secret",
        "https://example.com/org/repo.git",
    ],
)
def test_public_git_url_rejects_unsafe_forms(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", public_dns)
    with pytest.raises(ValueError):
        validate_public_git_url(url, ("github.com",))


def test_public_git_url_rejects_private_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
        ],
    )
    with pytest.raises(ValueError, match="non-public"):
        validate_public_git_url(
            "https://github.com/org/repo.git", ("github.com",)
        )


@pytest.mark.parametrize(
    "branch", ["-upload-pack=evil", "feature/../main", "bad branch", "topic@{1}"]
)
def test_git_branch_rejects_option_and_ref_injection(branch: str) -> None:
    with pytest.raises(ValueError):
        validate_git_branch(branch)


def test_redaction_preserves_shape_and_removes_secrets() -> None:
    source = (
        "password=super-secret\n"
        "api_key: abc123\n"
        "-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----\n"
    )
    redacted = redact_secrets(source)
    assert "super-secret" not in redacted
    assert "abc123" not in redacted
    assert "BEGIN PRIVATE KEY" not in redacted
    assert digest_secret("token") != "token"
    assert redacted.count("\n") == source.count("\n")


@pytest.mark.parametrize(
    "secret",
    [
        "my password is " + "hunter2",
        "密码是" + "qa-password-2026",
        "ghp_" + "a" * 36,
        "github_pat_" + "a" * 30,
        "AKIA" + "A" * 16,
        "xoxb-" + "1" * 12 + "-" + "a" * 24,
        ".".join(("eyJ" + "a" * 12, "b" * 16, "c" * 20)),
        "glpat-" + "a" * 20,
        "sk-proj-" + "a" * 40,
        "sk_live_" + "a" * 24,
        "hf_" + "a" * 34,
        "AIza" + "a" * 35,
        "AccountKey=" + "a" * 44,
    ],
)
def test_secret_detection_covers_common_credential_formats(secret: str) -> None:
    assert contains_secret(secret)


@pytest.mark.parametrize(
    "content",
    [
        "密码使用 Argon2 哈希后保存。",
        "Token 解析器会检查过期时间。",
        "用户偏好用中文解释代码调用链。",
        "Token is refreshed automatically.",
        "The API key is stored in Vault.",
        "password = Argon2 hashes are recommended.",
    ],
)
def test_secret_detection_keeps_safe_memory_content(content: str) -> None:
    assert not contains_secret(content)


def test_repository_file_blocks_traversal_and_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    inside = root / "src.py"
    inside.write_text("print('ok')", encoding="utf-8")
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")

    assert resolve_repository_file(root, "src.py") == inside
    with pytest.raises(ValueError, match="escapes"):
        resolve_repository_file(root, "../secret.txt")

    link = root / "linked.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        return
    with pytest.raises(ValueError, match="escapes"):
        resolve_repository_file(root, "linked.txt")
