from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from .database import create_database, initialize_database
from .index_job_schedule_lock import index_job_schedule_lock
from .indexing import IndexCoordinator
from .job_queue import IndexJobQueue, JobRequest
from .legacy_migration import migrate_sqlite_database
from .models import IndexJob, Repository, User
from .security import hash_password, validate_git_url
from .settings import Settings

DEMO_REPOSITORIES = (
    (
        "spring-rest",
        "Spring REST service guide",
        "https://github.com/spring-guides/gs-rest-service.git",
        "main",
        "Apache-2.0",
        "https://github.com/spring-guides/gs-rest-service/blob/main/LICENSE.code.txt",
    ),
    (
        "itsdangerous",
        "Python signing library",
        "https://github.com/pallets/itsdangerous.git",
        "main",
        "BSD-3-Clause",
        "https://github.com/pallets/itsdangerous/blob/main/LICENSE.txt",
    ),
    (
        "tslib",
        "TypeScript runtime helper library",
        "https://github.com/microsoft/tslib.git",
        "main",
        "0BSD",
        "https://github.com/microsoft/tslib/blob/main/LICENSE.txt",
    ),
)


def resources() -> tuple[Settings, Engine]:
    settings = Settings.load()
    engine = create_database(settings)
    initialize_database(settings, engine)
    return settings, engine


def create_admin(args) -> None:
    _settings, engine = resources()
    password = args.password or os.getenv(args.password_env, "")
    if not password:
        password = getpass.getpass("Password: ")
    email = args.email.strip().lower()
    with Session(engine) as session:
        if session.exec(select(User).where(User.email == email)).first():
            raise SystemExit("User already exists")
        session.add(User(
            email=email, display_name=args.name.strip(),
            password_hash=hash_password(password), role="admin",
        ))
        session.commit()
    print(f"Created administrator {email}")


def seed_demo(_args) -> None:
    settings, engine = resources()
    with Session(engine) as session:
        admin = session.exec(select(User).where(User.role == "admin")).first()
        if not admin:
            raise SystemExit("Create an administrator first")
        for (
            name,
            description,
            git_url,
            branch,
            license_name,
            license_url,
        ) in DEMO_REPOSITORIES:
            if session.exec(select(Repository).where(Repository.name == name)).first():
                continue
            session.add(Repository(
                name=name, description=description,
                git_url=validate_git_url(git_url, settings.allowed_git_hosts),
                branch=branch, visibility="public", license_name=license_name,
                license_url=license_url,
                created_by=admin.id,
            ))
        session.commit()
    print("Seeded demo repository definitions")


def index_demo(_args) -> None:
    settings, engine = resources()
    names = [item[0] for item in DEMO_REPOSITORIES]
    queue = IndexJobQueue(engine)
    with index_job_schedule_lock(engine):
        with Session(engine) as session:
            admin = session.exec(select(User).where(User.role == "admin")).first()
            repositories = session.exec(
                select(Repository)
                .where(col(Repository.name).in_(names))
                .order_by(Repository.id)
            ).all()
            if not admin:
                raise SystemExit("Create an administrator first")
            if len(repositories) != len(names):
                raise SystemExit("Run seed-demo before index-demo")
            jobs = [
                queue.add(
                    session,
                    JobRequest(repository_id=repository.id, created_by=admin.id),
                    skip_if_active=True,
                )
                for repository in repositories
            ]
            session.commit()
            job_ids = [job.id for job in jobs if job is not None]

    coordinator = IndexCoordinator(settings, engine)
    failures: list[str] = []
    try:
        for job_id in job_ids:
            coordinator._run(job_id)
            with Session(engine) as session:
                stored_job = session.get(IndexJob, job_id)
                if not stored_job or stored_job.status != "succeeded":
                    failures.append(
                        stored_job.error if stored_job else f"missing job {job_id}"
                    )
                else:
                    print(
                        f"Indexed {stored_job.repository_id} at {stored_job.commit[:8]}"
                    )
    finally:
        coordinator.shutdown()
    if failures:
        raise SystemExit("Demo indexing failed: " + "; ".join(failures))


def migrate_sqlite(args) -> None:
    settings, engine = resources()
    try:
        counts = migrate_sqlite_database(Path(args.sqlite).resolve(), engine)
    finally:
        engine.dispose()
    print(json.dumps(counts, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(prog="codeatlas")
    subparsers = parser.add_subparsers(dest="command", required=True)
    init = subparsers.add_parser("init-db")
    init.set_defaults(function=lambda _args: resources())
    admin = subparsers.add_parser("create-admin")
    admin.add_argument("--email", required=True)
    admin.add_argument("--name", default="CodeAtlas Admin")
    admin.add_argument("--password")
    admin.add_argument("--password-env", default="CODEATLAS_BOOTSTRAP_ADMIN_PASSWORD")
    admin.set_defaults(function=create_admin)
    demo = subparsers.add_parser("seed-demo")
    demo.set_defaults(function=seed_demo)
    index = subparsers.add_parser("index-demo")
    index.set_defaults(function=index_demo)
    migration = subparsers.add_parser("migrate-sqlite")
    migration.add_argument("--sqlite", required=True)
    migration.set_defaults(function=migrate_sqlite)
    args = parser.parse_args()
    args.function(args)


if __name__ == "__main__":
    main()
