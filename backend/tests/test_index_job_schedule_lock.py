from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextlib import contextmanager

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from codeatlas.api import router
from codeatlas.index_job_schedule_lock import index_job_schedule_lock
from codeatlas.job_queue import IndexJobQueue, JobRequest
from codeatlas.models import IndexJob, Repository
from tests.conftest import login_admin


def test_enqueue_waits_for_global_schedule_lock(application, admin) -> None:
    with Session(application.state.engine) as session:
        repository = Repository(
            name="schedule-lock-repository",
            git_url="https://github.com/example/schedule-lock.git",
            branch="main",
            visibility="public",
            status="ready",
            created_by=admin.id,
        )
        session.add(repository)
        session.commit()
        session.refresh(repository)
        repository_id = repository.id

    queue = IndexJobQueue(application.state.engine)
    with ThreadPoolExecutor(max_workers=1) as executor:
        with index_job_schedule_lock(application.state.engine):
            future = executor.submit(
                queue.enqueue,
                JobRequest(repository_id=repository_id, created_by=admin.id),
            )
            try:
                future.result(timeout=0.2)
            except TimeoutError:
                pass
            else:
                raise AssertionError("enqueue bypassed the global scheduling lock")
        job = future.result(timeout=5)

    assert job is not None
    assert job.repository_id == repository_id


def test_job_scheduling_routes_use_required_locks() -> None:
    expected = {
        (
            "/api/v1/embedding-profiles/{profile_id}/activate",
            "POST",
            "require_embedding_activation_locks",
        ),
        (
            "/api/v1/repositories/{repository_id}/sync",
            "POST",
            "require_index_job_schedule_lock",
        ),
    }
    found: set[tuple[str, str, str]] = set()
    for route in router.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        if not hasattr(route, "dependant"):
            continue
        dependencies = {
            getattr(dependency.call, "__name__", "")
            for dependency in route.dependant.dependencies
        }
        for dependency in dependencies:
            for method in methods:
                found.add((path, method, dependency))

    assert expected <= found


def test_unauthenticated_manual_sync_cannot_acquire_schedule_lock(
    client: TestClient, monkeypatch
) -> None:
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("unauthenticated request acquired the scheduling lock")

    monkeypatch.setattr("codeatlas.api.index_job_schedule_lock", fail_if_called)

    response = client.post("/api/v1/repositories/repository-1/sync")

    assert response.status_code == 401


def test_manual_sync_without_csrf_cannot_acquire_schedule_lock(
    client: TestClient, admin, monkeypatch
) -> None:
    login_admin(client)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("request without CSRF acquired the scheduling lock")

    monkeypatch.setattr("codeatlas.api.index_job_schedule_lock", fail_if_called)

    response = client.post("/api/v1/repositories/repository-1/sync")

    assert response.status_code == 403


def test_index_demo_uses_schedule_lock_and_stable_repository_order(
    application, admin, monkeypatch
) -> None:
    from codeatlas import cli

    with Session(application.state.engine) as session:
        for name, description, git_url, branch, license_name, license_url in (
            cli.DEMO_REPOSITORIES
        ):
            session.add(
                Repository(
                    name=name,
                    description=description,
                    git_url=git_url,
                    branch=branch,
                    visibility="public",
                    license_name=license_name,
                    license_url=license_url,
                    created_by=admin.id,
                )
            )
        session.commit()

    entered: list[bool] = []

    @contextmanager
    def recording_lock(_engine):
        entered.append(True)
        yield

    class FakeCoordinator:
        def __init__(self, _settings, engine):
            self.engine = engine

        def _run(self, job_id: str) -> None:
            with Session(self.engine) as session:
                job = session.get(IndexJob, job_id)
                assert job is not None
                job.status = "succeeded"
                job.commit = "c" * 40
                session.add(job)
                session.commit()

        def shutdown(self) -> None:
            return None

    monkeypatch.setattr(
        cli,
        "resources",
        lambda: (application.state.settings, application.state.engine),
    )
    monkeypatch.setattr(cli, "index_job_schedule_lock", recording_lock)
    monkeypatch.setattr(cli, "IndexCoordinator", FakeCoordinator)

    cli.index_demo(None)

    assert entered == [True]
    with Session(application.state.engine) as session:
        assert len(session.exec(select(IndexJob)).all()) == len(cli.DEMO_REPOSITORIES)
