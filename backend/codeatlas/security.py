from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import secrets
import socket
from pathlib import Path
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_PASSWORD_HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key)\b"
    r"\s*[:=]\s*)([^\s,;#]+)"
)
_PEM_BLOCK = re.compile(r"-----BEGIN [^-]+-----.*?-----END [^-]+-----", re.DOTALL)
_URL_CREDENTIALS = re.compile(r"(mysql|postgres|postgresql|mongodb|redis|https?)(\+[a-z]+)?://[^@]+@")
_BEARER_TOKEN = re.compile(r"(?i)bearer\s+[a-zA-Z0-9_\-\.=]{20,}")
_API_KEY_PATTERN = re.compile(r"(?i)(sk|pk|api|key|token|secret)[_-][a-zA-Z0-9]{20,}")
_SSH_GIT_URL = re.compile(
    r"^git@(?P<host>[A-Za-z0-9.-]+):"
    r"(?P<path>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*)\.git$"
)
_SAFE_REPOSITORY_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{1,79}$")
_SAFE_CREDENTIAL_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,199}$")


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("password must contain at least 12 characters")
    return _PASSWORD_HASHER.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _PASSWORD_HASHER.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def new_secret(prefix: str = "") -> str:
    return prefix + secrets.token_urlsafe(32)


def digest_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def validate_credential_ref(value: str) -> str:
    normalized = value.strip()
    if normalized.lower().startswith(("sk-", "pk-", "bearer ")):
        raise ValueError("credential_ref must be a server-side reference, not a secret value")
    if not _SAFE_CREDENTIAL_REF.fullmatch(normalized):
        raise ValueError(
            "credential_ref must contain only letters, numbers, dots, dashes or underscores"
        )
    return normalized


def mask_credential_ref(value: str) -> str:
    return "已配置" if value.strip() else "未配置"


def redact_secrets(text: str) -> str:
    def redact_pem(match: re.Match[str]) -> str:
        return "[REDACTED PRIVATE KEY]" + "\n" * match.group(0).count("\n")

    text = _PEM_BLOCK.sub(redact_pem, text)
    text = _URL_CREDENTIALS.sub(r"\1://[REDACTED]@", text)
    text = _BEARER_TOKEN.sub("Bearer [REDACTED]", text)
    text = _API_KEY_PATTERN.sub(r"\1_[REDACTED]", text)
    return _ASSIGNMENT.sub(r"\1[REDACTED]", text)


def validate_repository_name(name: str) -> str:
    normalized = name.strip().lower()
    if not _SAFE_REPOSITORY_NAME.fullmatch(normalized):
        raise ValueError(
            "repository name must use lowercase letters, numbers, dots, dashes or underscores"
        )
    return normalized


def validate_git_branch(branch: str) -> str:
    normalized = branch.strip()
    forbidden = ("..", "@{", "\\", "~", "^", ":", "?", "*", "[")
    if (
        not normalized
        or len(normalized) > 200
        or normalized.startswith(("-", ".", "/"))
        or normalized.endswith((".", "/"))
        or any(character.isspace() or ord(character) < 32 for character in normalized)
        or any(value in normalized for value in forbidden)
        or "//" in normalized
    ):
        raise ValueError("invalid Git branch name")
    return normalized


def validate_public_git_url(url: str, allowed_hosts: tuple[str, ...]) -> str:
    parsed = urlsplit(url.strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("only public HTTPS Git URLs are allowed")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Git URL must not include credentials, query parameters or fragments")
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname not in allowed_hosts:
        raise ValueError(f"Git host is not allowed: {hostname}")
    if not parsed.path.endswith(".git") or parsed.path.count("/") < 2:
        raise ValueError("Git URL must end with .git and include an owner and repository")
    if os.getenv("CODEATLAS_ALLOW_PRIVATE_GIT_HOSTS", "").lower() in {"1", "true", "yes"}:
        return parsed.geturl()
    for result in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM):
        address = ipaddress.ip_address(result[4][0])
        if not address.is_global:
            raise ValueError("Git host resolves to a non-public address")
    return parsed.geturl()


def validate_git_url(url: str, allowed_hosts: tuple[str, ...]) -> str:
    """Validate HTTPS URLs and GitHub-style SSH clone URLs."""
    normalized = url.strip()
    match = _SSH_GIT_URL.fullmatch(normalized)
    if match:
        host = match.group("host").lower().rstrip(".")
        if host not in allowed_hosts:
            raise ValueError(f"Git host is not allowed: {host}")
        return normalized
    return validate_public_git_url(normalized, allowed_hosts)


def resolve_repository_file(root: Path, relative_path: str) -> Path:
    if not relative_path or "\x00" in relative_path:
        raise ValueError("invalid repository path")
    resolved_root = root.resolve()
    requested = (resolved_root / relative_path).resolve()
    try:
        requested.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("path escapes the repository root") from exc
    if not requested.is_file():
        raise FileNotFoundError(relative_path)
    return requested
