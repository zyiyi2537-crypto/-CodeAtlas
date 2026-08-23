from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .security import validate_git_url
from .settings import Settings


class GitHubError(RuntimeError):
    """Raised when GitHub SSH operations fail."""


def repository_identity(repo_url: str) -> tuple[str, str]:
    """Return owner and repository name from an HTTPS or SCP-style URL."""
    normalized = repo_url.strip()
    if normalized.startswith("git@"):
        path = normalized.split(":", 1)[1]
        host = normalized.split("@", 1)[1].split(":", 1)[0]
        if host.lower() != "github.com":
            raise ValueError("GitHub SSH URL must use github.com")
    else:
        parsed = urlsplit(normalized)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() != "github.com":
            raise ValueError("GitHub repository URL must use github.com")
        path = parsed.path.lstrip("/")
    parts = path.removesuffix(".git").strip("/").split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError("GitHub repository URL must contain owner and repository")
    return parts[0], parts[1]


def ssh_key_directory(settings: Settings) -> Path:
    path = settings.data_dir / "ssh"
    path.mkdir(parents=True, exist_ok=True)
    return path


def generate_deploy_key(settings: Settings) -> tuple[str, str, str]:
    key_id = uuid4().hex
    directory = ssh_key_directory(settings)
    private_path = directory / f"github-{key_id}"
    public_path = private_path.with_suffix(".pub")
    private_key = Ed25519PrivateKey.generate()
    private_bytes = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.OpenSSH,
        serialization.NoEncryption(),
    )
    public_bytes = private_key.public_key().public_bytes(
        serialization.Encoding.OpenSSH,
        serialization.PublicFormat.OpenSSH,
    )
    public_value = public_bytes.decode("ascii") + f" codeatlas-{key_id}"
    private_path.write_bytes(private_bytes)
    public_path.write_text(public_value + "\n", encoding="ascii")
    if os.name != "nt":
        private_path.chmod(0o600)
        public_path.chmod(0o644)
    return key_id, public_value, str(private_path)


def resolve_deploy_key(settings: Settings, key_id: str) -> Path:
    if not key_id or "/" in key_id or "\\" in key_id or key_id.startswith("."):
        raise ValueError("Invalid GitHub deploy key id")
    path = (ssh_key_directory(settings) / f"github-{key_id}").resolve()
    if path.parent != ssh_key_directory(settings).resolve() or not path.is_file():
        raise ValueError("GitHub deploy key not found")
    return path


def ssh_command(settings: Settings, key_path: str | Path) -> str:
    known_hosts = ssh_key_directory(settings) / "known_hosts"
    return (
        f"ssh -i {shlex.quote(str(Path(key_path)))} -o IdentitiesOnly=yes "
        f"-o StrictHostKeyChecking=accept-new "
        f"-o UserKnownHostsFile={shlex.quote(str(known_hosts))}"
    )


def remote_commit(settings: Settings, git_url: str, branch: str, key_path: str | Path) -> str:
    validate_git_url(git_url, settings.allowed_git_hosts)
    try:
        result = subprocess.run(
            ["git", "ls-remote", git_url, f"refs/heads/{branch}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=settings.git_timeout_seconds,
            env={**os.environ, "GIT_SSH_COMMAND": ssh_command(settings, key_path)},
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise GitHubError(f"GitHub SSH check failed: {detail[-500:]}") from exc
    commit = result.stdout.split(maxsplit=1)[0] if result.stdout.strip() else ""
    if not commit:
        raise GitHubError("GitHub returned no commit for the configured branch")
    return commit
