from __future__ import annotations

import os
from datetime import UTC, datetime

from sqlmodel import Session, select

from .gitlab import GitLabClient, GitLabClientError
from .job_queue import IndexJobQueue
from .models import GitLabSource, Repository
from .settings import Settings
from .source_sync import (
    SourcePollingCoordinator,
    SourcePollingError,
    SourceRevision,
    SourceSchedule,
)


class GitLabSourceAdapter:
    def __init__(self, settings: Settings, engine):
        self.settings = settings
        self.engine = engine

    def schedules(self) -> list[SourceSchedule]:
        with Session(self.engine) as session:
            sources = session.exec(select(GitLabSource).where(GitLabSource.enabled)).all()
        return [
            SourceSchedule(source.id, source.last_checked_at, source.poll_interval_seconds)
            for source in sources
        ]

    def revisions(self, source_id: str) -> list[SourceRevision] | None:
        with Session(self.engine) as session:
            source = session.get(GitLabSource, source_id)
            if not source or not source.enabled:
                return None
            repositories = session.exec(
                select(Repository).where(Repository.source_id == source.id)
            ).all()
            config = (source.base_url, source.credential_ref)

        token = os.getenv(f"CODEATLAS_CREDENTIAL_{config[1].upper().replace('-', '_')}", "")
        if not token:
            raise SourcePollingError("GitLab credential reference is not configured")

        revisions = []
        try:
            with GitLabClient(config[0], token) as client:
                for repository in repositories:
                    if not repository.external_project_id:
                        continue
                    commit = client.project_branch_commit(
                        repository.external_project_id, repository.branch
                    )
                    revisions.append(
                        SourceRevision(
                            repository_id=repository.id,
                            created_by=repository.created_by,
                            local_commit=repository.last_commit,
                            remote_commit=commit,
                            message="Queued by GitLab commit check",
                        )
                    )
        except (ValueError, GitLabClientError) as exc:
            raise SourcePollingError(str(exc)) from exc
        return revisions

    def record_result(self, source_id: str, error: str) -> None:
        with Session(self.engine) as session:
            source = session.get(GitLabSource, source_id)
            if source:
                source.last_checked_at = datetime.now(UTC)
                source.last_error = error[:2000]
                session.add(source)
                session.commit()


class GitLabSyncCoordinator:
    """Compatibility facade for GitLab source polling."""

    def __init__(self, settings: Settings, engine, submit_job=None):
        queue = IndexJobQueue(engine, submit_job)
        self.coordinator = SourcePollingCoordinator(GitLabSourceAdapter(settings, engine), queue)

    def check_enabled_sources(self) -> int:
        return self.coordinator.check_enabled_sources()

    def check_source(self, source_id: str) -> int:
        return self.coordinator.check_source(source_id)
