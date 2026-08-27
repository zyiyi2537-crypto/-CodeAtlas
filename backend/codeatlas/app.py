from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from threading import Event, Thread

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session, select

from .api import router
from .database import create_database, initialize_database
from .external_sync import ExternalSourceSyncService
from .github_sync import GitHubSyncCoordinator
from .gitlab_sync import GitLabSyncCoordinator
from .indexing import IndexCoordinator
from .job_queue import IndexJobQueue
from .knowledge_search import KnowledgeSearch
from .mcp_server import build_mcp
from .models import ExternalSource, IndexJob
from .retrieval import CodeRetriever
from .settings import Settings

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.load()
    settings.ensure_directories()
    engine = create_database(settings)
    initialize_database(settings, engine)
    retriever = CodeRetriever(settings, engine)
    indexer = IndexCoordinator(settings, engine)

    def submit_job(job_id: str) -> None:
        indexer.submit(job_id)

    job_queue = IndexJobQueue(engine, submit_job)
    knowledge_search = KnowledgeSearch(engine, settings)
    external_sync = ExternalSourceSyncService(settings, engine, knowledge_search)
    gitlab_sync = GitLabSyncCoordinator(settings, engine, submit_job)
    github_sync = GitHubSyncCoordinator(settings, engine, submit_job)
    fastmcp, mcp_raw_app, mcp_http_app = build_mcp(
        settings, engine, retriever, knowledge_search=knowledge_search
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        with Session(engine) as database:
            queued = database.exec(select(IndexJob).where(IndexJob.status == "queued")).all()
            queued_external_sources = database.exec(
                select(ExternalSource).where(ExternalSource.sync_status == "queued")
            ).all()
        for job in queued:
            indexer.submit(job.id)
        for source in queued_external_sources:
            try:
                external_sync.submit(source.id)
            except RuntimeError:
                pass
        stop_sync = Event()

        def run_source_sync() -> None:
            coordinators = (
                ("gitlab", gitlab_sync),
                ("github", github_sync),
            )
            while not stop_sync.is_set():
                for provider, coordinator in coordinators:
                    try:
                        coordinator.check_enabled_sources()
                    except Exception:
                        logger.exception("%s source polling cycle failed", provider)
                with Session(engine) as database:
                    due_sources = database.exec(
                        select(ExternalSource).where(ExternalSource.enabled)
                    ).all()
                now = datetime.now(UTC)
                for source in due_sources:
                    checked = source.last_checked_at
                    if checked and checked.tzinfo is None:
                        checked = checked.replace(tzinfo=UTC)
                    if checked and (now - checked).total_seconds() < max(
                        300, source.poll_interval_seconds
                    ):
                        continue
                    try:
                        external_sync.submit(source.id)
                    except RuntimeError:
                        pass
                stop_sync.wait(60)

        sync_thread = Thread(target=run_source_sync, name="codeatlas-source-sync", daemon=True)
        sync_thread.start()
        async with mcp_raw_app.router.lifespan_context(mcp_raw_app):
            yield
        stop_sync.set()
        sync_thread.join(timeout=5)
        indexer.shutdown()
        external_sync.shutdown()

    app = FastAPI(
        title="CodeAtlas API",
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.retriever = retriever
    app.state.indexer = indexer
    app.state.job_queue = job_queue
    app.state.knowledge_search = knowledge_search
    app.state.external_sync = external_sync
    app.state.gitlab_sync = gitlab_sync
    app.state.github_sync = github_sync
    app.state.fastmcp = fastmcp
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.public_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", "X-CSRF-Token", "Authorization"],
    )
    app.include_router(router)

    @app.get("/api/v1/health")
    def health():
        return {"status": "ok", "service": "codeatlas", "version": "0.1.0"}

    @app.get("/api/v1/ready")
    def ready():
        with Session(engine) as database:
            database.exec(select(IndexJob).limit(1)).first()
        return {"status": "ready", "vector_chunks": retriever.vector_count()}

    app.mount("/mcp", mcp_http_app)
    return app
