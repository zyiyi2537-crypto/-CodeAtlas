from __future__ import annotations

import os
from datetime import UTC, datetime

from sqlmodel import Session, col, select

from .gitlab import GitLabClient, GitLabClientError
from .models import GitLabSource, IndexJob, Repository
from .settings import Settings


class GitLabSyncCoordinator:
    """Checks linked repositories and queues idempotent index jobs."""

    def __init__(self, settings: Settings, engine, submit_job=None):
        self.settings = settings
        self.engine = engine
        self.submit_job = submit_job

    def check_enabled_sources(self) -> int:
        with Session(self.engine) as session:
            source_ids = list(
                session.exec(select(GitLabSource.id).where(GitLabSource.enabled)).all()
            )
        return sum(self.check_source(source_id) for source_id in source_ids)

    def check_source(self, source_id: str) -> int:
        with Session(self.engine) as session:
            source = session.get(GitLabSource, source_id)
            if not source or not source.enabled:
                return 0
            repositories = session.exec(
                select(Repository).where(Repository.source_id == source.id)
            ).all()
            config = (source.base_url, source.credential_ref)

        token = os.getenv(
            f"CODEATLAS_CREDENTIAL_{config[1].upper().replace('-', '_')}", ""
        )
        if not token:
            self._record_source_error(source_id, "GitLab credential reference is not configured")
            return 0

        queued = 0
        try:
            with GitLabClient(config[0], token) as client:
                for repository in repositories:
                    if not repository.external_project_id:
                        continue
                    remote_commit = client.project_branch_commit(
                        repository.external_project_id, repository.branch
                    )
                    queued += self._queue_if_changed(
                        repository.id, remote_commit, repository.created_by
                    )
            self._record_source_check(source_id, "")
        except (ValueError, GitLabClientError) as exc:
            self._record_source_error(source_id, str(exc))
        return queued

    def _queue_if_changed(self, repository_id: str, remote_commit: str, created_by: str) -> int:
        with Session(self.engine) as session:
            repository = session.get(Repository, repository_id)
            if not repository or repository.last_commit == remote_commit:
                return 0
            active_job = session.exec(
                select(IndexJob).where(
                    IndexJob.repository_id == repository_id,
                    col(IndexJob.status).in_(["queued", "running"]),
                )
            ).first()
            if active_job:
                return 0
            job = IndexJob(
                    repository_id=repository_id,
                    created_by=created_by,
                    commit=remote_commit,
                    message="Queued by GitLab commit check",
                )
            session.add(job)
            session.commit()
            session.refresh(job)
            if self.submit_job is not None:
                self.submit_job(job.id)
            return 1

    def _record_source_check(self, source_id: str, error: str) -> None:
        with Session(self.engine) as session:
            source = session.get(GitLabSource, source_id)
            if source:
                source.last_checked_at = datetime.now(UTC)
                source.last_error = error[:2000]
                session.add(source)
                session.commit()

    def _record_source_error(self, source_id: str, error: str) -> None:
        self._record_source_check(source_id, error)
