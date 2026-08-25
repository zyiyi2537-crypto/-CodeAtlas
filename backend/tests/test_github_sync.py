from __future__ import annotations

from sqlmodel import Session, select

from codeatlas.github import generate_deploy_key
from codeatlas.github_sync import GitHubSyncCoordinator
from codeatlas.models import GitHubSource, IndexJob, Repository


def test_github_sync_queues_changed_commit(application, admin, monkeypatch) -> None:
    _key_id, _public_key, key_path = generate_deploy_key(application.state.settings)
    with Session(application.state.engine) as session:
        repo = Repository(
            name="hello-world",
            git_url="git@github.com:octocat/Hello-World.git",
            branch="main",
            visibility="public",
            created_by=admin.id,
            last_commit="old-commit",
        )
        session.add(repo)
        session.flush()
        source = GitHubSource(
            name="octocat-source",
            repo_url=repo.git_url,
            owner="octocat",
            repository="Hello-World",
            branch="main",
            repository_id=repo.id,
            ssh_key_path=key_path,
            created_by=admin.id,
        )
        session.add(source)
        session.commit()
        session.refresh(source)
        source_id = source.id
        repo_id = repo.id

    monkeypatch.setattr(
        "codeatlas.github_sync.remote_commit",
        lambda *_args: "new-commit",
    )
    coordinator = GitHubSyncCoordinator(application.state.settings, application.state.engine)
    assert coordinator.check_source(source_id) == 1
    assert coordinator.check_source(source_id) == 0
    with Session(application.state.engine) as session:
        jobs = session.exec(select(IndexJob).where(IndexJob.repository_id == repo_id)).all()
        assert len(jobs) == 1
        assert jobs[0].message == "Queued by GitHub commit check"


def test_public_github_sync_checks_https_without_ssh_key(
    application, admin, monkeypatch
) -> None:
    with Session(application.state.engine) as session:
        repo = Repository(
            name="yt-dlp",
            git_url="https://github.com/yt-dlp/yt-dlp.git",
            branch="master",
            visibility="public",
            created_by=admin.id,
            last_commit="old-commit",
        )
        session.add(repo)
        session.flush()
        source = GitHubSource(
            name="yt-dlp-public",
            repo_url=repo.git_url,
            owner="yt-dlp",
            repository="yt-dlp",
            branch="master",
            repository_id=repo.id,
            ssh_key_path="",
            created_by=admin.id,
        )
        session.add(source)
        session.commit()
        source_id = source.id

    captured: dict[str, str] = {}

    def remote_commit(_settings, git_url, branch, key_path=""):
        captured.update(git_url=git_url, branch=branch, key_path=key_path)
        return "new-commit"

    monkeypatch.setattr("codeatlas.github_sync.remote_commit", remote_commit)
    coordinator = GitHubSyncCoordinator(application.state.settings, application.state.engine)

    assert coordinator.check_source(source_id) == 1
    assert captured == {
        "git_url": "https://github.com/yt-dlp/yt-dlp.git",
        "branch": "master",
        "key_path": "",
    }
