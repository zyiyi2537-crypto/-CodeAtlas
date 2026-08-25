from __future__ import annotations

import subprocess

import httpx
import pytest

from codeatlas.github import GitHubBranchNotFoundError, GitHubError, remote_commit

PRIVATE_URL = "git@github.com:yt-dlp/yt-dlp.git"
PUBLIC_URL = "https://github.com/yt-dlp/yt-dlp.git"


def test_remote_commit_retries_transient_network_errors(settings, monkeypatch) -> None:
    attempts = 0

    def run(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise subprocess.TimeoutExpired("git", 30)
        return subprocess.CompletedProcess([], 0, stdout="a" * 40 + "\trefs/heads/master\n")

    monkeypatch.setattr("codeatlas.github.subprocess.run", run)
    monkeypatch.setattr("codeatlas.github.time.sleep", lambda _seconds: None)

    assert remote_commit(settings, PRIVATE_URL, "master") == "a" * 40
    assert attempts == 3


def test_remote_commit_distinguishes_missing_branch(settings, monkeypatch) -> None:
    monkeypatch.setattr(
        "codeatlas.github.subprocess.run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, stdout=""),
    )

    with pytest.raises(GitHubBranchNotFoundError, match="main"):
        remote_commit(settings, PRIVATE_URL, "main")


def test_remote_commit_reports_network_failure_after_bounded_retries(
    settings, monkeypatch
) -> None:
    attempts = 0

    def run(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise subprocess.TimeoutExpired("git", 30)

    monkeypatch.setattr("codeatlas.github.subprocess.run", run)
    monkeypatch.setattr("codeatlas.github.time.sleep", lambda _seconds: None)

    with pytest.raises(GitHubError, match="remote check failed"):
        remote_commit(settings, PRIVATE_URL, "master")
    assert attempts == 3


def test_public_remote_commit_uses_github_api_instead_of_git(settings, monkeypatch) -> None:
    monkeypatch.setenv("CODEATLAS_ALLOW_PRIVATE_GIT_HOSTS", "true")
    requests: list[str] = []
    client_type = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(200, json={"commit": {"sha": "b" * 40}})

    monkeypatch.setattr(
        "codeatlas.github.httpx.Client",
        lambda **_kwargs: client_type(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(
        "codeatlas.github.subprocess.run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("git must not run")),
    )

    assert remote_commit(settings, PUBLIC_URL, "master") == "b" * 40
    assert requests == ["https://api.github.com/repos/yt-dlp/yt-dlp/branches/master"]


def test_public_remote_commit_maps_api_404_to_missing_branch(settings, monkeypatch) -> None:
    monkeypatch.setenv("CODEATLAS_ALLOW_PRIVATE_GIT_HOSTS", "true")
    client_type = httpx.Client
    monkeypatch.setattr(
        "codeatlas.github.httpx.Client",
        lambda **_kwargs: client_type(
            transport=httpx.MockTransport(lambda _request: httpx.Response(404))
        ),
    )

    with pytest.raises(GitHubBranchNotFoundError, match="main"):
        remote_commit(settings, PUBLIC_URL, "main")
