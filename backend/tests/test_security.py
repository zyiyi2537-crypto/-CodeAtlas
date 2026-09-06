from __future__ import annotations

import json
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
    redacted = redact_secrets(secret)
    assert "[REDACTED]" in redacted
    assert not contains_secret(redacted)
    assert redact_secrets(redacted) == redacted


@pytest.mark.parametrize(
    "secret",
    [
        *[prefix + "a" * 36 for prefix in ("ghp_", "gho_", "ghu_", "ghs_", "ghr_")],
        "github_pat_" + "a" * 30 + "_",
        "glpat-" + "a" * 20 + "_-",
        "sk-proj-" + "a" * 40 + "_-",
        "sk-svcacct-" + "a" * 40 + "_-",
        *[prefix + "a" * 24 for prefix in ("sk_live_", "sk_test_", "pk_live_", "pk_test_")],
        "hf_" + "a" * 34,
        "AIza" + "a" * 35 + "_-",
        "AKIA" + "A" * 16,
        "ASIA" + "B" * 16,
        *[
            prefix + "1" * 12 + "-" + "a" * 24 + "-"
            for prefix in ("xoxb-", "xoxa-", "xoxp-", "xoxr-", "xoxs-")
        ],
        ".".join(("eyJ" + "a" * 12, "b" * 16, "c" * 20 + "_-")),
        "sk-" + "a" * 24 + "_-",
        "api_" + "a" * 24 + "_-",
    ],
)
@pytest.mark.parametrize("assignment", [False, True])
def test_provider_credentials_are_fully_redacted_without_overlap_fragments(
    secret: str, assignment: bool
) -> None:
    prefix = '"token": "' if assignment else '"credential": "'
    source = "{" + prefix + secret + '", "enabled": true}'
    expected = "{" + prefix + '[REDACTED]", "enabled": true}'
    assert contains_secret(source)
    redacted = redact_secrets(source)
    assert redacted == expected
    assert json.loads(redacted)["enabled"] is True
    assert not contains_secret(redacted)
    assert redact_secrets(redacted) == redacted


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('password="a b,#c\\\"d"; keep()', 'password="[REDACTED]"; keep()'),
        ("'password': 'a b,#c', 'enabled': True", "'password': '[REDACTED]', 'enabled': True"),
        ("password=demo-value; enabled=true", "password=[REDACTED]; enabled=true"),
        ('my password is "correct horse battery staple".', 'my password is "[REDACTED]".'),
        ("密码是qa-password-2026。", "密码是[REDACTED]。"),
        ("口令为qa-password-2026，已更换", "口令为[REDACTED]，已更换"),
        (
            "postgresql+psycopg://demo:fictional-password@db.example.test:5432/app",
            "postgresql+psycopg://[REDACTED]@db.example.test:5432/app",
        ),
        (
            'Authorization: Bearer ' + "a" * 24 + "+/==",
            "Authorization: Bearer [REDACTED]",
        ),
        (
            "AccountName=demo;AccountKey=" + "a" * 44 + "+/==;EndpointSuffix=example.test",
            "AccountName=demo;AccountKey=[REDACTED];EndpointSuffix=example.test",
        ),
    ],
)
def test_redaction_preserves_surrounding_structure(source: str, expected: str) -> None:
    assert contains_secret(source)
    redacted = redact_secrets(source)
    assert redacted == expected
    assert not contains_secret(redacted)
    assert redact_secrets(redacted) == redacted


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_redaction_preserves_original_line_endings(newline: str) -> None:
    source = newline.join(
        [
            "before()",
            "-----BEGIN PRIVATE KEY-----",
            "fictional-pem-content",
            "-----END PRIVATE KEY-----",
            "after()",
        ]
    )
    expected = newline.join(["before()", "[REDACTED]", "", "", "after()"])
    assert redact_secrets(source) == expected


@pytest.mark.parametrize(
    "content",
    [
        "密码使用 Argon2 哈希后保存。",
        "Token 解析器会检查过期时间。",
        "用户偏好用中文解释代码调用链。",
        "Token is refreshed automatically.",
        "The API key is stored in Vault.",
        "password = Argon2 hashes are recommended.",
        '"password": "bcrypt", "secret": "vault"',
        "password:\nthis is the next paragraph",
        "The token issuer validates expiry.",
        "token_refresh_configuration_enabled = True",
        "secret_scanning_configuration_enabled = True",
        "password=[REDACTED]",
        '"token": "[REDACTED]"',
        "https://[REDACTED]@example.test",
    ],
)
def test_secret_detection_keeps_safe_memory_content(content: str) -> None:
    assert not contains_secret(content)
    assert redact_secrets(content) == content


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
