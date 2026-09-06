from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import secrets
import socket
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_PASSWORD_HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
_SECRET_LABEL = (
    r"password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key"
)
_SECRET_VALUE = (
    r'"(?P<double_value>(?:\\[^\r\n]|[^"\\\r\n])*)"'
    r"|'(?P<single_value>(?:\\[^\r\n]|[^'\\\r\n])*)'"
    r"|(?P<bare_value>(?!\[REDACTED(?: PRIVATE KEY)?\])[^\s,;#\"'\[\]{}()<>。，；]+)"
)
_ASSIGNMENT = re.compile(
    rf"\b(?:{_SECRET_LABEL})\b[\"']?[ \t]*[:=][ \t]*(?:{_SECRET_VALUE})",
    re.IGNORECASE,
)
_PEM_BLOCK = re.compile(r"-----BEGIN [^-]+-----.*?-----END [^-]+-----", re.DOTALL)
_URL_CREDENTIALS = re.compile(
    r"(?:mysql|postgres|postgresql|mongodb|redis|https?)(?:\+[a-z]+)?://"
    r"(?P<value>[^/@\s]+)@",
    re.IGNORECASE,
)
_BEARER_TOKEN = re.compile(
    r"\bbearer[ \t]+(?P<value>[a-zA-Z0-9_\-.~+/=]{20,})", re.IGNORECASE
)
_API_KEY_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?:sk|pk|api|key|token|secret)[_-][A-Za-z0-9]{20,}[A-Za-z0-9_-]*"
    r"(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)
_NATURAL_LANGUAGE_SECRET = re.compile(
    r"(?P<label>\bmy[ \t]+password|\bpassword|\bpasswd|\bpwd|\bapi[ _-]?key|"
    r"access[ _-]?key|secret|token|密码|口令|密钥)"
    rf"[ \t]*(?:is\b|为|是|[:=])[ \t]*(?:{_SECRET_VALUE})",
    re.IGNORECASE,
)
_GITHUB_TOKEN = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,})(?![A-Za-z0-9_])"
)
_GITLAB_TOKEN = re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])")
_OPENAI_PROJECT_KEY = re.compile(
    r"\bsk-(?:proj|svcacct)-[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])"
)
_STRIPE_KEY = re.compile(r"\b(?:sk|pk)_(?:live|test)_[A-Za-z0-9]{16,}(?![A-Za-z0-9])")
_HUGGINGFACE_TOKEN = re.compile(r"\bhf_[A-Za-z0-9]{20,}(?![A-Za-z0-9])")
_GOOGLE_API_KEY = re.compile(r"\bAIza[A-Za-z0-9_-]{30,}(?![A-Za-z0-9_-])")
_AZURE_ACCOUNT_KEY = re.compile(
    r"\bAccountKey[ \t]*=[ \t]*(?P<value>[A-Za-z0-9+/]{32,}={0,2})", re.IGNORECASE
)
_AWS_ACCESS_KEY = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Za-z0-9_])")
_SLACK_TOKEN = re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}(?![A-Za-z0-9-])")
_JWT_TOKEN = re.compile(
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    r"(?![A-Za-z0-9_-])"
)
_SECRET_PATTERNS = (
    _PEM_BLOCK,
    _URL_CREDENTIALS,
    _BEARER_TOKEN,
    _API_KEY_PATTERN,
    _GITHUB_TOKEN,
    _GITLAB_TOKEN,
    _OPENAI_PROJECT_KEY,
    _STRIPE_KEY,
    _HUGGINGFACE_TOKEN,
    _GOOGLE_API_KEY,
    _AZURE_ACCOUNT_KEY,
    _AWS_ACCESS_KEY,
    _SLACK_TOKEN,
    _JWT_TOKEN,
)
_SAFE_SECRET_VALUES = frozenset(
    {"argon2", "bcrypt", "scrypt", "vault", "[redacted]", "[redacted private key]"}
)
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


def _secret_spans(text: str) -> Iterator[tuple[int, int]]:
    for pattern in _SECRET_PATTERNS:
        for match in pattern.finditer(text):
            group = "value" if "value" in pattern.groupindex else 0
            if match.group(group).lower() not in _SAFE_SECRET_VALUES:
                yield match.span(group)
    for pattern in (_ASSIGNMENT, _NATURAL_LANGUAGE_SECRET):
        for match in pattern.finditer(text):
            group = next(
                name for name in ("double_value", "single_value", "bare_value")
                if match.group(name) is not None
            )
            value = match.group(group)
            if not value or value.lower() in _SAFE_SECRET_VALUES:
                continue
            if pattern is _ASSIGNMENT:
                yield match.span(group)
                continue
            label = match.group("label").lower()
            if len(value) >= 6 and (
                label.startswith("my ")
                or label in {"密码", "口令", "密钥"}
                or len(value) >= 16
                or any(character.isdigit() or character in "_-./+=" for character in value)
            ):
                yield match.span(group)


def redact_secrets(text: str) -> str:
    # Match the original text so overlapping formats cannot leave credential fragments.
    spans: list[tuple[int, int]] = []
    for start, end in sorted(_secret_spans(text)):
        if spans and start <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(spans[-1][1], end))
        else:
            spans.append((start, end))
    parts: list[str] = []
    cursor = 0
    for start, end in spans:
        parts.extend((text[cursor:start], "[REDACTED]"))
        parts.append("".join(character for character in text[start:end] if character in "\r\n"))
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts)


def contains_secret(text: str) -> bool:
    """Return True when user-managed text contains a credential-like value."""
    return next(_secret_spans(text), None) is not None


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
