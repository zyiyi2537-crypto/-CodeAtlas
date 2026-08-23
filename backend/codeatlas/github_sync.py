from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Session, select

from .github import GitHubError, remote_commit
from .job_queue import IndexJobQueue
from .models import GitHubSource, Repository
from .settings import Settings
from .source_sync import (
    SourcePollingCoordinator,
    SourcePollingError,
    SourceRevision,
    SourceSchedule,
)


class GitHubSourceAdapter:
    def __init__(self, settings: Settings, engine):
        self.settings = settings
        self.engine = engine

    def schedules(self) -> list[SourceSchedule]:
        with Session(self.engine) as session:
            sources = session.exec(select(GitHubSource).where(GitHubSource.enabled)).all()
        return [
            SourceSchedule(source.id, source.last_checked_at, source.poll_interval_seconds)
            for source in sources
        ]

    def revisions(self, source_id: str) -> list[SourceRevision] | None:
        with Session(self.engine) as session:
            source = session.get(GitHubSource, source_id)
            if not source or not source.enabled:
                return None
            repository = session.get(Repository, source.repository_id)
            if not repository:
                raise SourcePollingError("Linked repository not found")
            config = (
                source.repo_url,
                source.branch,
                source.ssh_key_path,
                repository.id,
                repository.created_by,
                repository.last_commit,
            )
        try:
            commit = remote_commit(self.settings, config[0], config[1], config[2])
        except (ValueError, GitHubError) as exc:
            raise SourcePollingError(str(exc)) from exc
        return [
            SourceRevision(
                repository_id=config[3],
                created_by=config[4],
                local_commit=config[5],
                remote_commit=commit,
                message="Queued by GitHub commit check",
            )
        ]

    def record_result(self, source_id: str, error: str) -> None:
        with Session(self.engine) as session:
            source = session.get(GitHubSource, source_id)
            if source:
                source.last_checked_at = datetime.now(UTC)
                source.last_error = error[:2000]
                session.add(source)
                session.commit()


class GitHubSyncCoordinator:
    """Compatibility facade for GitHub source polling."""

    def __init__(self, settings: Settings, engine, submit_job=None):
        queue = IndexJobQueue(engine, submit_job)
        self.coordinator = SourcePollingCoordinator(GitHubSourceAdapter(settings, engine), queue)

    def check_enabled_sources(self) -> int:
        return self.coordinator.check_enabled_sources()

    def check_source(self, source_id: str) -> int:
        return self.coordinator.check_source(source_id)
