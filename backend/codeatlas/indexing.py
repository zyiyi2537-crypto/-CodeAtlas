from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from sqlmodel import Session, select

from .chunker import LANGUAGES, chunk_file
from .embeddings import EmbeddingClient, settings_for_profile
from .models import (
    CodeChunkRecord,
    EmbeddingProfile,
    GitHubSource,
    IndexGeneration,
    IndexJob,
    Repository,
)
from .repositories import remove_checkout, source_files, sync_repository
from .settings import Settings
from .vector_store import VectorStore, code_generation_namespace


def now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class IndexCoordinator:
    def __init__(self, settings: Settings, engine):
        self.settings = settings
        self.engine = engine
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="codeatlas-index")
        self.lock = threading.Lock()
        self.running_repositories: set[str] = set()

    def submit(self, job_id: str) -> None:
        self.executor.submit(self._run, job_id)

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)

    def _run(self, job_id: str) -> None:
        with Session(self.engine) as session:
            job = session.get(IndexJob, job_id)
            if not job:
                return
            repository = session.get(Repository, job.repository_id)
            if not repository:
                job.status = "failed"
                job.error = "Repository not found"
                session.add(job)
                session.commit()
                return
            with self.lock:
                if repository.id in self.running_repositories:
                    job.status = "failed"
                    job.error = "Another index job is already running for this repository"
                    session.add(job)
                    session.commit()
                    return
                self.running_repositories.add(repository.id)
        try:
            self._index(job_id)
        finally:
            with self.lock:
                self.running_repositories.discard(repository.id)

    def _progress(self, job_id: str, value: int, message: str) -> None:
        with Session(self.engine) as session:
            job = session.get(IndexJob, job_id)
            if job:
                job.progress = value
                job.message = message
                session.add(job)
                session.commit()

    def _index(self, job_id: str) -> None:
        generation_id = ""
        checkout_path = None
        embedding_settings = self.settings
        embedding_namespace = "default"
        generation_vector_namespace = ""
        try:
            with Session(self.engine) as session:
                job = session.get(IndexJob, job_id)
                repository = session.get(Repository, job.repository_id) if job else None
                if not job or not repository:
                    return
                job.status = "running"
                job.started_at = now()
                repository.status = "indexing"
                session.add(job)
                session.add(repository)
                session.commit()
                repository_id = repository.id
                space_id = repository.space_id
                git_url = repository.git_url
                branch = repository.branch
                requested_commit = job.commit
                github_source = session.exec(
                    select(GitHubSource).where(GitHubSource.repository_id == repository.id)
                ).first()
                ssh_key_path = github_source.ssh_key_path if github_source else ""
                embedding_profile = session.exec(
                    select(EmbeddingProfile).where(EmbeddingProfile.is_active)
                ).first()
                if embedding_profile:
                    embedding_settings = settings_for_profile(self.settings, embedding_profile)
                    embedding_namespace = embedding_profile.id

            self._progress(job_id, 10, "Synchronizing repository")
            root, commit = sync_repository(
                self.settings,
                repository_id,
                job_id,
                git_url,
                branch,
                ssh_key_path,
                commit=requested_commit,
            )
            checkout_path = root
            created_generation = IndexGeneration(repository_id=repository_id, commit=commit)
            with Session(self.engine) as session:
                session.add(created_generation)
                session.commit()
                session.refresh(created_generation)
                generation_id = created_generation.id
                job = session.get(IndexJob, job_id)
                if not job:
                    raise ValueError("index job was deleted while running")
                job.generation_id = generation_id
                job.commit = commit
                session.add(job)
                session.commit()

            generation_vector_namespace = code_generation_namespace(
                embedding_namespace, generation_id
            )
            self._progress(job_id, 25, "Chunking source files")
            files = source_files(root, LANGUAGES, self.settings.max_source_files)
            chunks = []
            for path in files:
                chunks.extend(chunk_file(path, root, repository_id, generation_id, commit))
            if not chunks:
                raise ValueError("repository contains no supported source files")

            self._progress(job_id, 45, f"Embedding {len(chunks)} code chunks")
            store = VectorStore(
                embedding_settings, namespace=generation_vector_namespace
            )
            store.add_generation(
                chunks,
                EmbeddingClient(embedding_settings),
                space_id=space_id,
            )
            self._progress(job_id, 80, "Activating search index")
            self._activate_generation(
                repository_id, space_id, generation_id, commit, str(root), chunks
            )

            with Session(self.engine) as session:
                job = session.get(IndexJob, job_id)
                active_generation = session.get(IndexGeneration, generation_id)
                repository = session.get(Repository, repository_id)
                job = session.get(IndexJob, job_id)
                if not active_generation or not repository or not job:
                    raise ValueError("index state disappeared before activation")
                previous_generation_id = repository.active_generation_id
                active_generation.status = "active"
                active_generation.chunk_count = len(chunks)
                active_generation.activated_at = now()
                repository.active_generation_id = generation_id
                repository.status = "ready"
                repository.local_path = str(root)
                repository.last_commit = commit
                repository.chunk_count = len(chunks)
                repository.last_indexed_at = now()
                job.status = "succeeded"
                job.progress = 100
                job.message = "Index is active"
                job.finished_at = now()
                if previous_generation_id:
                    previous = session.get(IndexGeneration, previous_generation_id)
                    if previous:
                        previous.status = "superseded"
                        session.add(previous)
                session.add(active_generation)
                session.add(repository)
                session.add(job)
                session.commit()
            if previous_generation_id:
                try:
                    previous_namespace = code_generation_namespace(
                        embedding_namespace, previous_generation_id
                    )
                    profile_store = VectorStore(
                        embedding_settings, namespace=embedding_namespace
                    )
                    if profile_store.has_namespace(previous_namespace):
                        profile_store.delete_namespace(previous_namespace)
                    else:
                        profile_store.delete_generation(previous_generation_id)
                except Exception:
                    pass
        except Exception as exc:
            if generation_id and generation_vector_namespace:
                self._discard_generation(
                    generation_id,
                    embedding_settings,
                    generation_vector_namespace,
                )
            if checkout_path is not None:
                try:
                    remove_checkout(self.settings, repository_id, checkout_path)
                except Exception:
                    pass
            with Session(self.engine) as session:
                job = session.get(IndexJob, job_id)
                if job:
                    job.status = "failed"
                    job.error = str(exc)[:2000]
                    job.finished_at = now()
                    session.add(job)
                    repository = session.get(Repository, job.repository_id)
                    if repository:
                        repository.status = (
                            "ready" if repository.active_generation_id else "failed"
                        )
                        session.add(repository)
                    session.commit()

    def _activate_generation(
        self,
        repository_id: str,
        space_id: str,
        generation_id: str,
        commit: str,
        root: str,
        chunks: list,
    ) -> None:
        with Session(self.engine) as session:
            session.add_all([
                CodeChunkRecord(
                    id=chunk.id,
                    generation_id=generation_id,
                    repository_id=repository_id,
                    space_id=space_id,
                    commit=commit,
                    path=chunk.path,
                    language=chunk.language,
                    symbol=chunk.symbol,
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    content=chunk.content,
                )
                for chunk in chunks
            ])
            session.commit()

    def _discard_generation(
        self,
        generation_id: str,
        embedding_settings: Settings,
        generation_vector_namespace: str,
    ) -> None:
        try:
            VectorStore(
                embedding_settings, namespace=generation_vector_namespace
            ).delete_namespace(generation_vector_namespace)
        except Exception:
            pass
        with Session(self.engine) as session:
            chunks = session.exec(
                select(CodeChunkRecord).where(CodeChunkRecord.generation_id == generation_id)
            ).all()
            for chunk in chunks:
                session.delete(chunk)
            generation = session.get(IndexGeneration, generation_id)
            if generation:
                generation.status = "failed"
                session.add(generation)
            session.commit()
