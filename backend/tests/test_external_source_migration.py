from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command


def test_external_source_migration_downgrades_on_mysql(
    mysql_database_url: str, monkeypatch
) -> None:
    monkeypatch.setenv("CODEATLAS_DATABASE_URL", mysql_database_url)
    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / "alembic.ini"))

    command.upgrade(config, "head")
    command.downgrade(config, "20260824_10")

    engine = create_engine(mysql_database_url)
    try:
        assert "externalsource" not in inspect(engine).get_table_names()
        assert "externalsourceitem" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()
