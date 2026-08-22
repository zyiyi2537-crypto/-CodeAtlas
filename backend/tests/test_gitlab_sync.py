from __future__ import annotations

from sqlmodel import Session, select

from codeatlas.gitlab_sync import GitLabSyncCoordinator
from codeatlas.models import GitLabSource, IndexJob, Repository


class FakeGitLabClient:
    commit = "new-commit-123"

    def __init__(self, *_args, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def project_branch_commit(self, project_id, branch):
        assert project_id == "101"
        assert branch == "main"
        return self.commit


def create_source_and_repo(application, admin):
    with Session(application.state.engine) as session:
        source = GitLabSource(
            name="company-gitlab",
            base_url="https://gitlab.example.com",
            group_path="platform",
            credential_ref="gitlab-platform-readonly",
            created_by=admin.id,
        )
        session.add(source)
        session.commit()
        session.refresh(source)
        repo = Repository(
            name="orders",
            git_url="https://gitlab.example.com/platform/orders.git",
            branch="main",
            visibility="private",
            source_id=source.id,
            external_project_id="101",
            created_by=admin.id,
            last_commit="old-commit",
        )
        session.add(repo)
        session.commit()
        session.refresh(repo)
        return source.id, repo.id


def test_sync_coordinator_queues_only_when_remote_commit_changes(
    application, admin, monkeypatch
) -> None:
    source_id, repo_id = create_source_and_repo(application, admin)
    monkeypatch.setenv("CODEATLAS_CREDENTIAL_GITLAB_PLATFORM_READONLY", "test-token")
    monkeypatch.setattr("codeatlas.gitlab_sync.GitLabClient", FakeGitLabClient)
    coordinator = GitLabSyncCoordinator(application.state.settings, application.state.engine)

    assert coordinator.check_source(source_id) == 1
    assert coordinator.check_source(source_id) == 0
    with Session(application.state.engine) as session:
        jobs = session.exec(select(IndexJob).where(IndexJob.repository_id == repo_id)).all()
        assert len(jobs) == 1
        assert jobs[0].commit == "new-commit-123"


def test_sync_coordinator_does_not_duplicate_active_job(application, admin, monkeypatch) -> None:
    source_id, repo_id = create_source_and_repo(application, admin)
    monkeypatch.setenv("CODEATLAS_CREDENTIAL_GITLAB_PLATFORM_READONLY", "test-token")
    monkeypatch.setattr("codeatlas.gitlab_sync.GitLabClient", FakeGitLabClient)
    with Session(application.state.engine) as session:
        session.add(IndexJob(repository_id=repo_id, created_by=admin.id, commit="new-commit-123"))
        session.commit()
    coordinator = GitLabSyncCoordinator(application.state.settings, application.state.engine)

    assert coordinator.check_source(source_id) == 0
