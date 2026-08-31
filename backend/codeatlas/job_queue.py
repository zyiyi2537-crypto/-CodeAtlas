from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlmodel import Session, col, select

from .index_job_schedule_lock import index_job_schedule_connection_lock
from .member_lifecycle_lock import member_lifecycle_lock
from .models import IndexJob, Repository


class RepositoryNotFoundError(LookupError):
    pass


class ActiveIndexJobError(RuntimeError):
    pass


@dataclass(frozen=True)
class JobRequest:
    repository_id: str
    created_by: str
    message: str = ""
    commit: str = ""


class IndexJobQueue:
    """Owns the active-job invariant and post-commit worker submission."""

    def __init__(self, engine, submit_job: Callable[[str], None] | None = None):
        self.engine = engine
        self.submit_job = submit_job

    def add(
        self,
        session: Session,
        request: JobRequest,
        *,
        skip_if_active: bool = False,
        skip_if_latest_commit: bool = False,
        use_repository_owner: bool = False,
    ) -> IndexJob | None:
        repository = session.exec(
            select(Repository).where(Repository.id == request.repository_id).with_for_update()
        ).first()
        if repository is None:
            raise RepositoryNotFoundError(request.repository_id)

        active = session.exec(
            select(IndexJob).where(
                IndexJob.repository_id == request.repository_id,
                col(IndexJob.status).in_(("queued", "running")),
            )
        ).first()
        if active is not None:
            if skip_if_active:
                return None
            raise ActiveIndexJobError(request.repository_id)

        if skip_if_latest_commit and request.commit:
            latest_commit = session.exec(
                select(IndexJob.commit)
                .where(IndexJob.repository_id == request.repository_id)
                .order_by(col(IndexJob.created_at).desc(), col(IndexJob.id).desc())
            ).first()
            if latest_commit == request.commit:
                return None

        job = IndexJob(
            repository_id=request.repository_id,
            created_by=repository.created_by if use_repository_owner else request.created_by,
            message=request.message,
            commit=request.commit,
        )
        session.add(job)
        session.flush()
        return job

    def enqueue(
        self,
        request: JobRequest,
        *,
        skip_if_active: bool = False,
        skip_if_latest_commit: bool = False,
    ) -> IndexJob | None:
        with member_lifecycle_lock(self.engine) as connection:
            with index_job_schedule_connection_lock(connection):
                with Session(connection) as session:
                    job = self.add(
                        session,
                        request,
                        skip_if_active=skip_if_active,
                        skip_if_latest_commit=skip_if_latest_commit,
                        use_repository_owner=True,
                    )
                    session.commit()
                    if job is None:
                        return None
                    session.refresh(job)
                    job_id = job.id
                connection.commit()
            self.submit((job_id,))
            return job

    def submit(self, job_ids: tuple[str, ...] | list[str]) -> None:
        if self.submit_job is None:
            return
        for job_id in job_ids:
            self.submit_job(job_id)
