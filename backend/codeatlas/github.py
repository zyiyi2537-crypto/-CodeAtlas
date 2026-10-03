from __future__ import annotations

import os
import re
import shlex
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .security import validate_git_url
from .settings import Settings


class GitHubError(RuntimeError):
    """Raised when GitHub SSH operations fail."""


class GitHubBranchNotFoundError(GitHubError):
    """Raised when the configured branch is not present on the remote."""


GITHUB_NETWORK_ATTEMPTS = 3


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


def remote_commit(
    settings: Settings,
    git_url: str,
    branch: str,
    key_path: str | Path = "",
) -> str:
    validate_git_url(git_url, settings.allowed_git_hosts)
    if git_url.startswith("https://github.com/"):
        owner, repository = repository_identity(git_url)
        api_url = f"https://api.github.com/repos/{owner}/{repository}/branches/{branch}"
        for attempt in range(1, GITHUB_NETWORK_ATTEMPTS + 1):
            try:
                with httpx.Client(
                    timeout=min(settings.git_timeout_seconds, 30),
                    headers={"Accept": "application/vnd.github+json"},
                ) as client:
                    response = client.get(api_url)
                if response.status_code == 404:
                    raise GitHubBranchNotFoundError(
                        f"GitHub branch does not exist: {branch}"
                    )
                response.raise_for_status()
                commit = str(response.json().get("commit", {}).get("sha", ""))
                if len(commit) != 40:
                    raise GitHubError("GitHub API returned an invalid commit")
                return commit
            except GitHubBranchNotFoundError:
                raise
            except (httpx.HTTPError, ValueError, GitHubError) as exc:
                if attempt == GITHUB_NETWORK_ATTEMPTS:
                    raise GitHubError(f"GitHub API check failed: {str(exc)[-500:]}") from exc
                time.sleep(attempt * 2)
    environment = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    if key_path:
        environment["GIT_SSH_COMMAND"] = ssh_command(settings, key_path)
    result = None
    for attempt in range(1, GITHUB_NETWORK_ATTEMPTS + 1):
        try:
            result = subprocess.run(
                ["git", "ls-remote", git_url, f"refs/heads/{branch}"],
                check=True,
                capture_output=True,
                text=True,
                timeout=settings.git_timeout_seconds,
                env=environment,
            )
            break
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            if attempt == GITHUB_NETWORK_ATTEMPTS:
                detail = getattr(exc, "stderr", "") or str(exc)
                raise GitHubError(f"GitHub remote check failed: {detail[-500:]}") from exc
            time.sleep(attempt * 2)
    commit = result.stdout.split(maxsplit=1)[0] if result and result.stdout.strip() else ""
    if not commit:
        raise GitHubBranchNotFoundError(f"GitHub branch does not exist: {branch}")
    return commit


def validate_public_commit(settings: Settings, git_url: str, commit: str) -> str:
    """Verify that a pinned revision exists in a public GitHub repository."""
    validate_git_url(git_url, settings.allowed_git_hosts)
    if not git_url.startswith("https://github.com/") or not re.fullmatch(
        r"[0-9a-fA-F]{40}", commit
    ):
        raise ValueError("Pinned commits require a public GitHub URL and a 40-character SHA")
    owner, repository = repository_identity(git_url)
    url = f"https://api.github.com/repos/{owner}/{repository}/commits/{commit}"
    try:
        with httpx.Client(
            timeout=min(settings.git_timeout_seconds, 30),
            headers={"Accept": "application/vnd.github+json"},
        ) as client:
            response = client.get(url)
        if response.status_code == 404:
            raise ValueError("Pinned commit does not exist in the configured GitHub repository")
        response.raise_for_status()
        resolved = str(response.json().get("sha", ""))
        if resolved.lower() != commit.lower():
            raise GitHubError("GitHub API returned a different commit than the requested pin")
        return resolved.lower()
    except ValueError:
        raise
    except (httpx.HTTPError, GitHubError) as exc:
        raise GitHubError(f"GitHub commit check failed: {str(exc)[-500:]}") from exc
