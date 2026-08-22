from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from .models import SQLModel

TABLES = (
    "user",
    "usersession",
    "repository",
    "repositoryaccess",
    "apitoken",
    "indexjob",
    "indexgeneration",
    "codechunkrecord",
    "auditevent",
)


def migrate_sqlite_database(sqlite_path: Path, destination: Engine) -> dict[str, int]:
    """Copy legacy business tables into an empty MySQL CodeAtlas schema."""
    if not sqlite_path.is_file():
        raise FileNotFoundError(f"SQLite database does not exist: {sqlite_path}")

    tables = SQLModel.metadata.tables
    with sqlite3.connect(sqlite_path) as source:
        source.row_factory = sqlite3.Row
        source_counts = {
            name: int(source.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0])
            for name in TABLES
        }
        with destination.begin() as target:
            target_counts_before = {
                name: int(
                    target.execute(
                        select(func.count()).select_from(tables[name])
                    ).scalar_one()
                )
                for name in TABLES
            }
            nonempty = {
                name: count for name, count in target_counts_before.items() if count
            }
            if nonempty:
                details = ", ".join(
                    f"{name}={count}" for name, count in nonempty.items()
                )
                raise RuntimeError(f"MySQL destination must be empty; found {details}")

            for name in TABLES:
                rows = [dict(row) for row in source.execute(f'SELECT * FROM "{name}"')]
                if rows:
                    target.execute(tables[name].insert(), rows)

            target_counts = {
                name: int(
                    target.execute(
                        select(func.count()).select_from(tables[name])
                    ).scalar_one()
                )
                for name in TABLES
            }

    if source_counts != target_counts:
        raise RuntimeError(
            "Migration count verification failed: "
            f"source={source_counts}, destination={target_counts}"
        )
    return target_counts
