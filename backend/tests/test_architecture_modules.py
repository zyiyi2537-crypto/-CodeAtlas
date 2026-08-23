from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, select

from codeatlas.job_queue import ActiveIndexJobError, IndexJobQueue, JobRequest
from codeatlas.knowledge_search import KnowledgeSearch
from codeatlas.models import (
    Document,
    DocumentChunkRecord,
    DocumentCollection,
    IndexJob,
    Repository,
    WikiPage,
)
from codeatlas.source_sync import (
    SourcePollingCoordinator,
    SourceRevision,
    SourceSchedule,
)


def create_repository(application, admin, name: str = "queue-test") -> Repository:
    repository = Repository(
        name=name,
        git_url="https://github.com/example/repository.git",
        branch="main",
        visibility="private",
        created_by=admin.id,
    )
    with Session(application.state.engine) as session:
        session.add(repository)
        session.commit()
        session.refresh(repository)
    return repository


def test_job_queue_commits_before_submit_and_rejects_duplicates(application, admin) -> None:
    repository = create_repository(application, admin)
    submitted: list[str] = []

    def submit(job_id: str) -> None:
        with Session(application.state.engine) as session:
            assert session.get(IndexJob, job_id) is not None
        submitted.append(job_id)

    queue = IndexJobQueue(application.state.engine, submit)
    job = queue.enqueue(JobRequest(repository.id, admin.id, message="manual"))

    assert job is not None
    assert submitted == [job.id]
    assert queue.enqueue(JobRequest(repository.id, admin.id), skip_if_active=True) is None
    with pytest.raises(ActiveIndexJobError):
        queue.enqueue(JobRequest(repository.id, admin.id))


def test_source_schedule_respects_poll_interval() -> None:
    now = datetime.now(UTC)
    assert SourceSchedule("new", None, 300).is_due(now)
    assert not SourceSchedule("recent", now - timedelta(seconds=299), 300).is_due(now)
    assert SourceSchedule("due", now - timedelta(seconds=300), 300).is_due(now)
    assert SourceSchedule("naive", (now - timedelta(seconds=300)).replace(tzinfo=None), 300).is_due(
        now
    )


class FakeSourceAdapter:
    def __init__(self, revision: SourceRevision):
        self.revision = revision
        self.results: list[tuple[str, str]] = []

    def schedules(self) -> list[SourceSchedule]:
        return [SourceSchedule("source-1", None, 300)]

    def revisions(self, source_id: str) -> list[SourceRevision]:
        assert source_id == "source-1"
        return [self.revision]

    def record_result(self, source_id: str, error: str) -> None:
        self.results.append((source_id, error))


def test_source_polling_uses_shared_queue_policy(application, admin) -> None:
    repository = create_repository(application, admin, "poll-test")
    adapter = FakeSourceAdapter(
        SourceRevision(
            repository_id=repository.id,
            created_by=admin.id,
            local_commit="old",
            remote_commit="new",
            message="Queued by test source",
        )
    )
    coordinator = SourcePollingCoordinator(adapter, IndexJobQueue(application.state.engine))

    assert coordinator.check_enabled_sources() == 1
    assert coordinator.check_enabled_sources() == 0
    assert adapter.results == [("source-1", ""), ("source-1", "")]
    with Session(application.state.engine) as session:
        jobs = session.exec(select(IndexJob).where(IndexJob.repository_id == repository.id)).all()
    assert len(jobs) == 1
    assert jobs[0].commit == "new"


def test_knowledge_search_owns_document_and_wiki_results(application, admin) -> None:
    collection = DocumentCollection(name="Architecture", created_by=admin.id)
    document = Document(
        collection_id=collection.id,
        title="Index queue",
        original_filename="queue.md",
        mime_type="text/markdown",
        source_path="queue.md",
        sha256="0" * 64,
        created_by=admin.id,
    )
    chunk = DocumentChunkRecord(
        id="doc-chunk",
        document_id=document.id,
        collection_id=collection.id,
        title=document.title,
        section="Safety",
        content="Commit the index job before worker submission.",
    )
    wiki = WikiPage(
        path="architecture/index-queue.md",
        title="Index queue",
        content="The queue prevents duplicate active jobs.",
        sources_json=json.dumps(["document://queue"]),
        created_by=admin.id,
    )
    collection_id = collection.id
    document_id = document.id
    wiki_path = wiki.path
    with Session(application.state.engine) as session:
        session.add(collection)
        session.flush()
        session.add(document)
        session.flush()
        session.add(chunk)
        session.add(wiki)
        session.commit()

    search = KnowledgeSearch(application.state.engine)
    documents = search.search_documents("worker submission", [collection_id])
    pages = search.search_wiki("duplicate active")

    assert documents[0]["document_id"] == document_id
    assert pages[0]["path"] == wiki_path
    assert search.get_wiki_page(wiki_path)["sources"] == ["document://queue"]
    with pytest.raises(ValueError):
        search.search_documents("   ")
