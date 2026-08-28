from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command


def test_embedding_credential_migration_upgrades_and_downgrades_on_mysql(
    mysql_database_url: str, monkeypatch
) -> None:
    monkeypatch.setenv("CODEATLAS_DATABASE_URL", mysql_database_url)
    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / "alembic.ini"))

    command.upgrade(config, "20260825_11")
    engine = create_engine(mysql_database_url)
    try:
        before = {column["name"] for column in inspect(engine).get_columns("embeddingprofile")}
        assert "api_key_ciphertext" not in before
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(mysql_database_url)
    try:
        columns = {column["name"] for column in inspect(engine).get_columns("embeddingprofile")}
        assert "api_key_ciphertext" in columns
        with engine.connect() as connection:
            nullable = connection.execute(
                text(
                    "SELECT IS_NULLABLE FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() "
                    "AND table_name = 'embeddingprofile' "
                    "AND column_name = 'api_key_ciphertext'"
                )
            ).scalar_one()
        assert nullable == "NO"
    finally:
        engine.dispose()

    command.downgrade(config, "20260825_11")
    engine = create_engine(mysql_database_url)
    try:
        after = {column["name"] for column in inspect(engine).get_columns("embeddingprofile")}
        assert "api_key_ciphertext" not in after
    finally:
        engine.dispose()
