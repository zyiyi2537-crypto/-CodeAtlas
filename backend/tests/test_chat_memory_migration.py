from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command


def test_chat_memory_migration_upgrades_and_downgrades_on_mysql(
    mysql_database_url: str, monkeypatch
) -> None:
    monkeypatch.setenv("CODEATLAS_DATABASE_URL", mysql_database_url)
    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / "alembic.ini"))

    command.upgrade(config, "20260829_13")
    engine = create_engine(mysql_database_url)
    try:
        assert not {"chatsession", "chatmessage", "usermemory"} & set(
            inspect(engine).get_table_names()
        )
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(mysql_database_url)
    try:
        inspector = inspect(engine)
        assert {"chatsession", "chatmessage", "usermemory"} <= set(
            inspector.get_table_names()
        )
        assert {column["name"] for column in inspector.get_columns("chatsession")} == {
            "id",
            "user_id",
            "request_id",
            "title",
            "repository_ids_json",
            "message_count",
            "created_at",
            "updated_at",
        }
        assert {column["name"] for column in inspector.get_columns("chatmessage")} == {
            "id",
            "session_id",
            "user_id",
            "role",
            "sequence",
            "request_id",
            "content",
            "citations_json",
            "created_at",
        }
        assert {column["name"] for column in inspector.get_columns("usermemory")} == {
            "id",
            "user_id",
            "kind",
            "content",
            "content_hash",
            "created_at",
            "updated_at",
        }
        chat_uniques = {
            tuple(item["column_names"])
            for item in inspector.get_unique_constraints("chatmessage")
        }
        session_uniques = {
            tuple(item["column_names"])
            for item in inspector.get_unique_constraints("chatsession")
        }
        memory_uniques = {
            tuple(item["column_names"])
            for item in inspector.get_unique_constraints("usermemory")
        }
        assert ("session_id", "sequence") in chat_uniques
        assert ("session_id", "request_id") in chat_uniques
        assert ("user_id", "request_id") in session_uniques
        assert ("user_id", "kind", "content_hash") in memory_uniques
        for table in ("chatsession", "chatmessage", "usermemory"):
            assert inspector.get_foreign_keys(table)
    finally:
        engine.dispose()

    command.downgrade(config, "20260830_14")
    engine = create_engine(mysql_database_url)
    try:
        inspector = inspect(engine)
        assert "request_id" not in {
            column["name"] for column in inspector.get_columns("chatmessage")
        }
        assert "request_id" not in {
            column["name"] for column in inspector.get_columns("chatsession")
        }
        assert {"chatsession", "chatmessage", "usermemory"} <= set(
            inspector.get_table_names()
        )
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(mysql_database_url)
    try:
        assert "request_id" in {
            column["name"] for column in inspect(engine).get_columns("chatmessage")
        }
        assert "request_id" in {
            column["name"] for column in inspect(engine).get_columns("chatsession")
        }
    finally:
        engine.dispose()

    command.downgrade(config, "20260829_13")
    engine = create_engine(mysql_database_url)
    try:
        assert not {"chatsession", "chatmessage", "usermemory"} & set(
            inspect(engine).get_table_names()
        )
    finally:
        engine.dispose()
