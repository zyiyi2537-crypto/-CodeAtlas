from __future__ import annotations

from pathlib import Path

import pytest
from git import Repo
from git.cmd import Git
from sqlalchemy import create_engine, text
from sqlmodel import Session

from codeatlas.database import create_database, initialize_database
from codeatlas.indexing import IndexCoordinator
from codeatlas.legacy_migration import migrate_sqlite_database
from codeatlas.models import (
    CodeChunkRecord,
    IndexGeneration,
    IndexJob,
    Repository,
    SQLModel,
    User,
)
from codeatlas.repositories import sync_repository
from codeatlas.retrieval import CodeRetriever
from codeatlas.settings import Settings


def seed_job(engine, repository_name: str = "demo") -> tuple[Repository, IndexJob]:
    user = User(
        email=f"{repository_name}@example.com",
        display_name="Admin",
        password_hash="not-used",
        role="admin",
    )
    repository = Repository(
        name=repository_name,
        git_url="https://github.com/org/demo.git",
        branch="main",
        visibility="public",
        created_by=user.id,
    )
    job = IndexJob(repository_id=repository.id, created_by=user.id)
    with Session(engine) as session:
        session.add(user)
        session.commit()
        session.add(repository)
        session.commit()
        session.add(job)
        session.commit()
        session.refresh(repository)
        session.refresh(job)
    return repository, job


def test_index_success_search_version_switch_and_failure_rollback(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_database(settings)
    initialize_database(settings, engine)
    repository, first_job = seed_job(engine)
    source = tmp_path / "source"
    source.mkdir()
    code = source / "service.py"
    code.write_text(
        "def calculate_invoice(total):\n    return total * 2\n",
        encoding="utf-8",
    )
    commits = iter(["a" * 40, "b" * 40, "c" * 40])
    monkeypatch.setattr(
        "codeatlas.indexing.sync_repository",
        lambda *_args, **_kwargs: (source, next(commits)),
    )
    coordinator = IndexCoordinator(settings, engine)
    coordinator._run(first_job.id)

    with Session(engine) as session:
        first = session.get(IndexJob, first_job.id)
        current = session.get(Repository, repository.id)
        assert first is not None and first.status == "succeeded"
        assert current is not None and current.active_generation_id
        first_generation_id = current.active_generation_id
        second_job = IndexJob(repository_id=repository.id, created_by=first_job.created_by)
        session.add(second_job)
        session.commit()
        session.refresh(second_job)

    results = CodeRetriever(settings, engine).search("calculate invoice")
    assert results
    assert results[0]["path"] == "service.py"
    assert results[0]["commit"] == "a" * 40

    code.write_text(
        "def calculate_invoice(total):\n    return total * 3  # updated\n",
        encoding="utf-8",
    )
    coordinator._run(second_job.id)
    with Session(engine) as session:
        current = session.get(Repository, repository.id)
        first_generation = session.get(IndexGeneration, first_generation_id)
        assert current is not None
        assert current.active_generation_id != first_generation_id
        assert current.last_commit == "b" * 40
        assert first_generation is not None and first_generation.status == "superseded"
        active_generation_id = current.active_generation_id
        failed_job = IndexJob(repository_id=repository.id, created_by=first_job.created_by)
        session.add(failed_job)
        session.commit()
        session.refresh(failed_job)

    code.unlink()
    coordinator._run(failed_job.id)
    with Session(engine) as session:
        current = session.get(Repository, repository.id)
        failed = session.get(IndexJob, failed_job.id)
        assert current is not None and current.active_generation_id == active_generation_id
        assert current.status == "ready"
        assert failed is not None and failed.status == "failed"
        failed_generation = session.get(IndexGeneration, failed.generation_id)
        assert failed_generation is not None and failed_generation.status == "failed"
    with engine.connect() as connection:
        count = connection.execute(
            text("SELECT count(*) FROM codechunkrecord WHERE generation_id = :generation_id"),
            {"generation_id": failed.generation_id},
        ).scalar_one()
        assert count == 0
    coordinator.shutdown()


def test_initialize_database_requeues_interrupted_jobs(settings: Settings) -> None:
    engine = create_database(settings)
    initialize_database(settings, engine)
    _repository, job = seed_job(engine, "recovery")
    with Session(engine) as session:
        stored = session.get(IndexJob, job.id)
        assert stored is not None
        stored.status = "running"
        stored.progress = 55
        session.add(stored)
        session.commit()

    initialize_database(settings, engine)
    with Session(engine) as session:
        recovered = session.get(IndexJob, job.id)
        assert recovered is not None
        assert recovered.status == "queued"
        assert recovered.progress == 0
        assert "Recovered" in recovered.message


def test_git_sync_uses_incremental_cache_and_immutable_worktrees(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream_path = tmp_path / "upstream"
    upstream = Repo.init(upstream_path)
    upstream.git.checkout("-b", "main")
    (upstream_path / "demo.py").write_text("value = 1\n", encoding="utf-8")
    upstream.index.add(["demo.py"])
    upstream.index.commit("first")
    monkeypatch.setattr(
        "codeatlas.repositories.validate_public_git_url", lambda url, _hosts: url
    )

    first_path, first_commit = sync_repository(
        settings, "repository", "job-one", str(upstream_path), "main"
    )
    (upstream_path / "demo.py").write_text("value = 2\n", encoding="utf-8")
    upstream.index.add(["demo.py"])
    upstream.index.commit("second")
    second_path, second_commit = sync_repository(
        settings, "repository", "job-two", str(upstream_path), "main"
    )

    assert first_commit != second_commit
    assert (first_path / "demo.py").read_text(encoding="utf-8") == "value = 1\n"
    assert (second_path / "demo.py").read_text(encoding="utf-8") == "value = 2\n"
    assert (settings.repositories_dir / ".cache" / "repository" / ".git").is_dir()


def test_git_sync_retries_transient_clone_failure_and_cleans_partial_cache(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream_path = tmp_path / "upstream-retry"
    upstream = Repo.init(upstream_path)
    upstream.git.checkout("-b", "main")
    (upstream_path / "demo.py").write_text("value = 1\n", encoding="utf-8")
    upstream.index.add(["demo.py"])
    upstream.index.commit("first")
    monkeypatch.setattr(
        "codeatlas.repositories.validate_public_git_url", lambda url, _hosts: url
    )
    original_execute = Git.execute
    attempts = 0

    def flaky_execute(self, command, *args, **kwargs):
        nonlocal attempts
        if len(command) > 1 and command[1] == "clone":
            attempts += 1
        if attempts == 1 and len(command) > 1 and command[1] == "clone":
            partial = Path(command[-1])
            partial.mkdir(parents=True, exist_ok=True)
            (partial / "partial").write_text("incomplete", encoding="utf-8")
            raise OSError("Failed to connect to github.com port 443: Connection timed out")
        return original_execute(self, command, *args, **kwargs)

    monkeypatch.setattr("git.cmd.Git.execute", flaky_execute)
    monkeypatch.setattr("codeatlas.repositories.time.sleep", lambda _seconds: None)

    checkout, commit = sync_repository(
        settings, "retry-repository", "retry-job", str(upstream_path), "main"
    )

    assert attempts == 2
    assert commit == upstream.head.commit.hexsha
    assert (checkout / "demo.py").read_text(encoding="utf-8") == "value = 1\n"
    assert not (settings.repositories_dir / ".cache" / "retry-repository" / "partial").exists()


def test_public_github_sync_uses_codeload_snapshot(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEATLAS_ALLOW_PRIVATE_GIT_HOSTS", "true")
    source = tmp_path / "snapshot-source"
    nested = source / "yt-dlp-commit"
    nested.mkdir(parents=True)
    (nested / "demo.py").write_text("value = 1\n", encoding="utf-8")
    archive = tmp_path / "snapshot.tar.gz"
    import tarfile

    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(nested, arcname=nested.name)
    requested: list[str] = []

    def download(url: str, destination: Path, _timeout: int) -> None:
        requested.append(url)
        destination.write_bytes(archive.read_bytes())

    monkeypatch.setattr("codeatlas.repositories._download_file", download)

    checkout, commit = sync_repository(
        settings,
        "public-snapshot",
        "snapshot-job",
        "https://github.com/yt-dlp/yt-dlp.git",
        "master",
        commit="c" * 40,
    )

    assert commit == "c" * 40
    assert requested == [
        "https://codeload.github.com/yt-dlp/yt-dlp/tar.gz/" + "c" * 40
    ]
    assert (checkout / "demo.py").read_text(encoding="utf-8") == "value = 1\n"


def test_public_github_sync_resolves_commit_before_codeload(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEATLAS_ALLOW_PRIVATE_GIT_HOSTS", "true")
    source = tmp_path / "snapshot-source-resolved"
    nested = source / "public-snapshot-commit"
    nested.mkdir(parents=True)
    (nested / "demo.py").write_text("value = 2\n", encoding="utf-8")
    archive = tmp_path / "resolved-snapshot.tar.gz"
    import tarfile

    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(nested, arcname=nested.name)
    resolved: list[tuple[str, str]] = []
    requested: list[str] = []

    def resolve(_settings, git_url: str, branch: str, _key_path="") -> str:
        resolved.append((git_url, branch))
        return "d" * 40

    def download(url: str, destination: Path, _timeout: int) -> None:
        requested.append(url)
        destination.write_bytes(archive.read_bytes())

    monkeypatch.setattr("codeatlas.github.remote_commit", resolve)
    monkeypatch.setattr("codeatlas.repositories._download_file", download)

    checkout, commit = sync_repository(
        settings,
        "public-snapshot-resolved",
        "resolved-job",
        "https://github.com/example/public-snapshot.git",
        "main",
    )

    assert commit == "d" * 40
    assert resolved == [("https://github.com/example/public-snapshot.git", "main")]
    assert requested == [
        "https://codeload.github.com/example/public-snapshot/tar.gz/" + "d" * 40
    ]
    assert (checkout / "demo.py").read_text(encoding="utf-8") == "value = 2\n"


def test_repository_scope_isolates_private_repositories(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_database(settings)
    initialize_database(settings, engine)
    private, job = seed_job(engine, "private-demo")
    source = tmp_path / "private-source"
    source.mkdir()
    (source / "secret.py").write_text(
        "def internal_calculation():\n    return 42\n", encoding="utf-8"
    )
    with Session(engine) as session:
        stored = session.get(Repository, private.id)
        assert stored is not None
        stored.visibility = "private"
        session.add(stored)
        session.commit()
    monkeypatch.setattr(
        "codeatlas.indexing.sync_repository",
        lambda *_args, **_kwargs: (source, "d" * 40),
    )
    coordinator = IndexCoordinator(settings, engine)
    coordinator._run(job.id)
    retriever = CodeRetriever(settings, engine)
    assert retriever.search("internal calculation") == []
    scoped = retriever.search(
        "internal calculation", scope_repository_ids=(private.id,)
    )
    assert scoped and scoped[0]["repo"] == private.id
    coordinator.shutdown()


def test_legacy_sqlite_migration_preserves_rows_and_fulltext_search(
    settings: Settings, tmp_path: Path
) -> None:
    legacy_path = tmp_path / "legacy-codeatlas.db"
    source_engine = create_engine(f"sqlite:///{legacy_path.as_posix()}")
    SQLModel.metadata.create_all(source_engine)
    user = User(
        email="migration@example.com",
        display_name="Migration Admin",
        password_hash="not-used",
        role="admin",
    )
    repository = Repository(
        name="migration-demo",
        git_url="https://github.com/org/migration-demo.git",
        branch="main",
        visibility="public",
        status="ready",
        created_by=user.id,
    )
    generation = IndexGeneration(
        repository_id=repository.id,
        commit="e" * 40,
        status="active",
        chunk_count=1,
    )
    repository.active_generation_id = generation.id
    repository.last_commit = generation.commit
    repository.chunk_count = 1
    chunk = CodeChunkRecord(
        id="f" * 64,
        generation_id=generation.id,
        repository_id=repository.id,
        commit=generation.commit,
        path="src/invoice_service.py",
        language="python",
        symbol="calculate_invoice",
        start_line=1,
        end_line=2,
        content="def calculate_invoice(total):\n    return total * 2",
    )
    with Session(source_engine) as source:
        source.add(user)
        source.commit()
        source.add(repository)
        source.commit()
        source.add(generation)
        source.commit()
        source.add(chunk)
        source.commit()
    source_engine.dispose()

    destination = create_database(settings)
    initialize_database(settings, destination)
    counts = migrate_sqlite_database(legacy_path, destination)

    assert counts["user"] == 1
    assert counts["repository"] == 1
    assert counts["codechunkrecord"] == 1
    results = CodeRetriever(settings, destination).search("calculate invoice")
    assert results and results[0]["symbol"] == "calculate_invoice"
