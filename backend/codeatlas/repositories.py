from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from git import Git, Repo

from .security import validate_git_branch, validate_public_git_url
from .settings import Settings

EXCLUDED_DIRECTORIES = {
    ".git", ".idea", ".vscode", "node_modules", "vendor", "dist", "build",
    "target", "coverage", ".venv", "venv", "__pycache__", ".next", ".nuxt",
}
EXCLUDED_FILENAMES = {"package-lock.json", "pnpm-lock.yaml", "yarn.lock"}


def _managed_path(settings: Settings, *parts: str) -> Path:
    root = settings.repositories_dir.resolve()
    target = root.joinpath(*parts).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("repository path escapes the managed directory") from exc
    return target


def sync_repository(
    settings: Settings,
    repository_id: str,
    checkout_id: str,
    git_url: str,
    branch: str,
) -> tuple[Path, str]:
    git_url = validate_public_git_url(git_url, settings.allowed_git_hosts)
    branch = validate_git_branch(branch)
    cache = _managed_path(settings, ".cache", repository_id)
    checkout = _managed_path(settings, repository_id, checkout_id)
    git_environment = {
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_LFS_SKIP_SMUDGE": "1",
        "GIT_HTTP_LOW_SPEED_LIMIT": "1024",
        "GIT_HTTP_LOW_SPEED_TIME": "30",
    }
    old_environment = {key: os.environ.get(key) for key in git_environment}
    os.environ.update(git_environment)
    timeout_options = (
        {} if sys.platform == "win32" else {"kill_after_timeout": settings.git_timeout_seconds}
    )
    try:
        if checkout.exists():
            raise ValueError("repository checkout already exists")
        if not cache.exists():
            cache.parent.mkdir(parents=True, exist_ok=True)
            Git().clone(
                git_url,
                str(cache),
                branch=branch,
                depth=1,
                single_branch=True,
                no_tags=True,
                **timeout_options,
            )
        else:
            repo = Repo(cache)
            origin = repo.remotes.origin
            if next(origin.urls) != git_url:
                raise ValueError("managed repository remote does not match configured URL")
            repo.git.fetch(
                "origin",
                f"+refs/heads/{branch}:refs/remotes/origin/{branch}",
                "--depth=1",
                "--no-tags",
                **timeout_options,
            )
        repo = Repo(cache)
        commit = repo.commit(f"origin/{branch}").hexsha
        checkout.parent.mkdir(parents=True, exist_ok=True)
        repo.git.worktree(
            "add",
            "--detach",
            str(checkout),
            commit,
            **timeout_options,
        )
        checkout_repo = Repo(checkout)
        if checkout_repo.submodules:
            raise ValueError("Git submodules are not supported")
        size = directory_size(checkout)
        if size > settings.max_repository_mb * 1024 * 1024:
            raise ValueError(f"repository exceeds {settings.max_repository_mb} MB")
        return checkout, commit
    except Exception:
        if checkout.exists():
            shutil.rmtree(checkout)
        if cache.exists():
            try:
                Repo(cache).git.worktree("prune")
            except Exception:
                pass
        if cache.exists() and not (cache / ".git").is_dir():
            shutil.rmtree(cache)
        raise
    finally:
        for key, value in old_environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def remove_checkout(settings: Settings, repository_id: str, checkout_path: Path) -> None:
    expected_parent = _managed_path(settings, repository_id)
    resolved = checkout_path.resolve()
    if resolved.parent != expected_parent:
        raise ValueError("refusing to remove an unmanaged repository checkout")
    if resolved.exists():
        shutil.rmtree(resolved)
    cache = _managed_path(settings, ".cache", repository_id)
    if cache.exists():
        Repo(cache).git.worktree("prune")


def directory_size(root: Path) -> int:
    total = 0
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            total += path.stat().st_size
    return total


def source_files(root: Path, languages: dict[str, str], max_files: int) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        relative_parts = path.relative_to(root).parts
        if any(part in EXCLUDED_DIRECTORIES for part in relative_parts[:-1]):
            continue
        if path.name in EXCLUDED_FILENAMES or path.suffix.lower() not in languages:
            continue
        if path.stat().st_size > 1024 * 1024:
            continue
        files.append(path)
        if len(files) > max_files:
            raise ValueError(f"repository exceeds the source file limit of {max_files}")
    return sorted(files)
