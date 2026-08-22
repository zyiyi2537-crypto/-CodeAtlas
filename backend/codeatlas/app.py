from __future__ import annotations

from contextlib import asynccontextmanager
from threading import Event, Thread

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session, select

from .api import router
from .database import create_database, initialize_database
from .gitlab_sync import GitLabSyncCoordinator
from .indexing import IndexCoordinator
from .mcp_server import build_mcp
from .models import IndexJob
from .retrieval import CodeRetriever
from .settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.load()
    settings.ensure_directories()
    engine = create_database(settings)
    initialize_database(settings, engine)
    retriever = CodeRetriever(settings, engine)
    indexer = IndexCoordinator(settings, engine)
    gitlab_sync = GitLabSyncCoordinator(settings, engine, indexer.submit)
    fastmcp, mcp_raw_app, mcp_http_app = build_mcp(settings, engine, retriever)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        with Session(engine) as database:
            queued = database.exec(select(IndexJob).where(IndexJob.status == "queued")).all()
        for job in queued:
            indexer.submit(job.id)
        stop_sync = Event()

        def run_gitlab_sync() -> None:
            while not stop_sync.is_set():
                gitlab_sync.check_enabled_sources()
                stop_sync.wait(60)

        sync_thread = Thread(target=run_gitlab_sync, name="codeatlas-gitlab-sync", daemon=True)
        sync_thread.start()
        async with mcp_raw_app.router.lifespan_context(mcp_raw_app):
            yield
        stop_sync.set()
        sync_thread.join(timeout=5)
        indexer.shutdown()

    app = FastAPI(
        title="CodeAtlas API", version="0.1.0", docs_url="/api/docs",
        openapi_url="/api/openapi.json", lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.retriever = retriever
    app.state.indexer = indexer
    app.state.gitlab_sync = gitlab_sync
    app.state.fastmcp = fastmcp
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.public_origin], allow_credentials=True,
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
        return {"status": "ready", "vector_chunks": retriever.vector_store.count()}

    app.mount("/mcp", mcp_http_app)
    return app


