from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlmodel import Session, select

from codeatlas.connectors import ExternalItem
from codeatlas.external_sync import ExternalSourceSyncService
from codeatlas.job_queue import IndexJobQueue, JobRequest
from codeatlas.models import (
    Document,
    DocumentCollection,
    ExternalSource,
    IndexJob,
    Repository,
    User,
)
from codeatlas.source_sync import SourcePollingCoordinator, SourceRevision, SourceSchedule


class BlockingConnector:
    def __init__(self, snapshot_taken: Event, allow_continue: Event):
        self.snapshot_taken = snapshot_taken
        self.allow_continue = allow_continue

    def list_items(self) -> list[ExternalItem]:
        self.snapshot_taken.set()
        assert self.allow_continue.wait(timeout=10)
        return [
            ExternalItem(
                external_id="docs/ownership.md",
                path="docs/ownership.md",
                title="Ownership",
                filename="ownership.md",
                mime_type="text/markdown",
                revision="v1",
                size=20,
            )
        ]

    def fetch(self, _item: ExternalItem) -> bytes:
        return b"# Ownership\n\nCurrent owner wins."

    def close(self) -> None:
        return None


class StaleRevisionAdapter:
    def __init__(
        self,
        revision: SourceRevision,
        snapshot_taken: Event,
        allow_continue: Event,
    ):
        self.revision = revision
        self.snapshot_taken = snapshot_taken
        self.allow_continue = allow_continue
        self.results: list[tuple[str, str]] = []

    def schedules(self) -> list[SourceSchedule]:
        return [SourceSchedule("source-1", None, 300)]

    def revisions(self, source_id: str) -> list[SourceRevision]:
        assert source_id == "source-1"
        self.snapshot_taken.set()
        assert self.allow_continue.wait(timeout=10)
        return [self.revision]

    def record_result(self, source_id: str, error: str) -> None:
        self.results.append((source_id, error))


def _member(email: str) -> User:
    return User(
        email=email,
        display_name="Worker owner",
        password_hash="not-used",
        role="member",
    )


def _login_admin(client: TestClient) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": "admin@example.com",
            "password": "correct horse battery staple",
        },
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 200
    return str(response.json()["csrf_token"])


def test_external_sync_reloads_owner_after_account_assets_are_transferred(
    application, admin, monkeypatch
) -> None:
    old_owner = _member("external-worker-owner@example.com")
    with Session(application.state.engine) as session:
        session.add(old_owner)
        session.flush()
        collection = DocumentCollection(
            name="Worker ownership",
            description="",
            created_by=old_owner.id,
        )
        session.add(collection)
        session.flush()
        source = ExternalSource(
            name="Worker ownership source",
            provider="aws_s3",
            collection_id=collection.id,
            credential_ref="worker-test",
            config_json='{"bucket":"worker-test"}',
            created_by=old_owner.id,
        )
        session.add(source)
        session.commit()
        old_owner_id = old_owner.id
        collection_id = collection.id
        source_id = source.id

    snapshot_taken = Event()
    allow_continue = Event()
    connector = BlockingConnector(snapshot_taken, allow_continue)
    monkeypatch.setattr("codeatlas.external_sync.build_connector", lambda _source: connector)
    service = ExternalSourceSyncService(
        application.state.settings,
        application.state.engine,
        application.state.knowledge_search,
    )
    admin_client = TestClient(application, raise_server_exceptions=False)
    try:
        admin_csrf = _login_admin(admin_client)
        with ThreadPoolExecutor(max_workers=1) as executor:
            sync = executor.submit(service.sync_source, source_id)
            assert snapshot_taken.wait(timeout=10)
            deleted = admin_client.delete(
                f"/api/v1/members/{old_owner_id}",
                headers={"X-CSRF-Token": admin_csrf},
            )
            assert deleted.status_code == 204
            allow_continue.set()
            result = sync.result(timeout=20)

        assert result.created == 1
        with Session(application.state.engine) as session:
            document = session.exec(
                select(Document).where(Document.collection_id == collection_id)
            ).one()
            assert document.created_by == admin.id
            assert session.get(User, old_owner_id) is None
    finally:
        allow_continue.set()
        service.shutdown()
        admin_client.close()


def test_source_polling_reloads_repository_owner_before_enqueuing_job(
    application, admin
) -> None:
    old_owner = _member("repository-worker-owner@example.com")
    with Session(application.state.engine) as session:
        session.add(old_owner)
        session.flush()
        repository = Repository(
            name="worker-owner-repository",
            git_url="https://github.com/example/worker-owner.git",
            branch="main",
            visibility="private",
            created_by=old_owner.id,
            last_commit="old",
        )
        session.add(repository)
        session.commit()
        old_owner_id = old_owner.id
        repository_id = repository.id

    stale_revision = SourceRevision(
        repository_id=repository_id,
        created_by=old_owner_id,
        local_commit="old",
        remote_commit="new",
        message="Queued after owner snapshot",
    )
    snapshot_taken = Event()
    allow_continue = Event()
    adapter = StaleRevisionAdapter(stale_revision, snapshot_taken, allow_continue)
    coordinator = SourcePollingCoordinator(
        adapter,
        IndexJobQueue(application.state.engine),
    )
    admin_client = TestClient(application, raise_server_exceptions=False)
    try:
        admin_csrf = _login_admin(admin_client)
        with ThreadPoolExecutor(max_workers=1) as executor:
            polling = executor.submit(coordinator.check_source, "source-1")
            assert snapshot_taken.wait(timeout=10)
            deleted = admin_client.delete(
                f"/api/v1/members/{old_owner_id}",
                headers={"X-CSRF-Token": admin_csrf},
            )
            assert deleted.status_code == 204
            allow_continue.set()
            assert polling.result(timeout=20) == 1

        with Session(application.state.engine) as session:
            job = session.exec(
                select(IndexJob).where(IndexJob.repository_id == repository_id)
            ).one()
            assert job.created_by == admin.id
            assert session.get(User, old_owner_id) is None
    finally:
        allow_continue.set()
        admin_client.close()


def test_background_job_enqueue_reuses_one_connection_for_named_locks(
    application, admin
) -> None:
    engine = create_engine(
        application.state.engine.url,
        pool_size=1,
        max_overflow=0,
        pool_timeout=1,
        pool_pre_ping=True,
    )
    try:
        with Session(engine) as session:
            repository = Repository(
                name="single-connection-worker-owner",
                git_url="https://github.com/example/single-connection.git",
                branch="main",
                visibility="private",
                created_by=admin.id,
            )
            session.add(repository)
            session.commit()
            repository_id = repository.id

        queue = IndexJobQueue(engine)
        job = queue.enqueue(
            JobRequest(repository_id=repository_id, created_by=admin.id)
        )

        assert job is not None
        assert job.created_by == admin.id
    finally:
        engine.dispose()
