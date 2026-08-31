from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.engine import Engine, make_url
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, col, create_engine, select

from .models import ExternalSource, IndexJob
from .settings import Settings


def create_database(settings: Settings) -> Engine:
    settings.ensure_directories()
    database_url = make_url(settings.database_url)
    if database_url.get_backend_name() != "mysql":
        raise ValueError("CODEATLAS_DATABASE_URL must use a MySQL SQLAlchemy driver")
    options: dict = {
        "pool_pre_ping": True,
        "pool_recycle": 1800,
    }
    if settings.environment == "test":
        options["poolclass"] = NullPool
    return create_engine(
        database_url,
        **options,
    )


def initialize_database(settings: Settings, engine) -> None:
    if settings.environment == "test":
        SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        jobs = session.exec(select(IndexJob).where(IndexJob.status == "running")).all()
        for job in jobs:
            job.status = "queued"
            job.progress = 0
            job.message = "Recovered after service restart"
            session.add(job)
        external_sources = session.exec(
            select(ExternalSource).where(
                col(ExternalSource.sync_status).in_(("queued", "syncing"))
            )
        ).all()
        for source in external_sources:
            source.sync_status = "queued"
            source.last_error = "Recovered after service restart"
            session.add(source)
        session.commit()


def session_dependency(engine) -> Iterator[Session]:
    with Session(engine) as session:
        yield session
