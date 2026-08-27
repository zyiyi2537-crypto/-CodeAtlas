from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from .job_queue import IndexJobQueue, JobRequest


class SourcePollingError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceRevision:
    repository_id: str
    created_by: str
    local_commit: str
    remote_commit: str
    message: str


@dataclass(frozen=True)
class SourceSchedule:
    source_id: str
    last_checked_at: datetime | None
    poll_interval_seconds: int

    def is_due(self, now: datetime) -> bool:
        if self.last_checked_at is None:
            return True
        last_checked_at = self.last_checked_at
        if last_checked_at.tzinfo is None:
            last_checked_at = last_checked_at.replace(tzinfo=UTC)
        return (now - last_checked_at).total_seconds() >= max(60, self.poll_interval_seconds)


class SourcePollingAdapter(Protocol):
    def schedules(self) -> list[SourceSchedule]: ...
    def revisions(self, source_id: str) -> list[SourceRevision] | None: ...
    def record_result(self, source_id: str, error: str) -> None: ...


class SourcePollingCoordinator:
    """Owns polling lifecycle while provider adapters own remote access."""

    def __init__(self, adapter: SourcePollingAdapter, job_queue: IndexJobQueue):
        self.adapter = adapter
        self.job_queue = job_queue

    def check_enabled_sources(self) -> int:
        now = datetime.now(UTC)
        return sum(
            self.check_source(schedule.source_id)
            for schedule in self.adapter.schedules()
            if schedule.is_due(now)
        )

    def check_source(self, source_id: str) -> int:
        try:
            revisions = self.adapter.revisions(source_id)
            if revisions is None:
                return 0
            queued = 0
            for revision in revisions:
                if revision.local_commit == revision.remote_commit:
                    continue
                job = self.job_queue.enqueue(
                    JobRequest(
                        repository_id=revision.repository_id,
                        created_by=revision.created_by,
                        commit=revision.remote_commit,
                        message=revision.message,
                    ),
                    skip_if_active=True,
                    skip_if_latest_commit=True,
                )
                queued += int(job is not None)
            self.adapter.record_result(source_id, "")
            return queued
        except SourcePollingError as exc:
            self.adapter.record_result(source_id, str(exc))
            return 0
