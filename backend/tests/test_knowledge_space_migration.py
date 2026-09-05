from __future__ import annotations

import json
from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command


def test_knowledge_space_migration_round_trips_existing_mysql_data(
    mysql_database_url: str,
    monkeypatch,
) -> None:
    monkeypatch.setenv("CODEATLAS_DATABASE_URL", mysql_database_url)
    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / "alembic.ini"))

    command.upgrade(config, "20260831_15")
    engine = create_engine(mysql_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO user "
                    "(id, email, display_name, password_hash, role, is_active, created_at) "
                    "VALUES ('owner-before-spaces', 'owner-before-spaces@example.com', "
                    "'Owner', 'hash', 'owner', 1, CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO repository "
                    "(id, name, description, git_url, branch, visibility, license_name, "
                    "license_url, local_path, status, chunk_count, last_commit, created_by, "
                    "created_at) VALUES ('repo-before-spaces', 'before-spaces', '', "
                    "'https://github.com/example/before-spaces.git', 'main', 'private', '', "
                    "'', '', 'pending', 0, '', 'owner-before-spaces', CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO apitoken "
                    "(id, name, token_prefix, token_hash, scopes_json, repository_ids_json, "
                    "created_by, created_at) VALUES ('token-before-spaces', 'Before spaces', "
                    "'cat_before', 'token-before-spaces-hash', '[\"read\"]', "
                    "'[\"repo-before-spaces\"]', 'owner-before-spaces', CURRENT_TIMESTAMP)"
                )
            )
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(mysql_database_url)
    try:
        inspector = inspect(engine)
        assert {"workspace", "knowledgespace", "spacegrant", "companyconvention"} <= set(
            inspector.get_table_names()
        )
        for table in (
            "repository",
            "documentcollection",
            "documentchunkrecord",
            "wikipage",
            "codechunkrecord",
        ):
            assert "space_id" in {
                column["name"] for column in inspector.get_columns(table)
            }
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT space_id FROM repository WHERE id='repo-before-spaces'")
            ).scalar_one() == "default-space"
            assert json.loads(
                connection.execute(
                    text(
                        "SELECT space_ids_json FROM apitoken "
                        "WHERE id='token-before-spaces'"
                    )
                ).scalar_one()
            ) == ["default-space"]
            assert connection.execute(
                text("SELECT COUNT(*) FROM workspace WHERE id='default-workspace'")
            ).scalar_one() == 1
            assert connection.execute(
                text("SELECT COUNT(*) FROM knowledgespace WHERE id='default-space'")
            ).scalar_one() == 1
    finally:
        engine.dispose()

    command.downgrade(config, "20260831_15")
    engine = create_engine(mysql_database_url)
    try:
        inspector = inspect(engine)
        assert not {"workspace", "knowledgespace", "spacegrant", "companyconvention"} & set(
            inspector.get_table_names()
        )
        assert "space_id" not in {
            column["name"] for column in inspector.get_columns("repository")
        }
        assert "space_ids_json" not in {
            column["name"] for column in inspector.get_columns("apitoken")
        }
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT name FROM repository WHERE id='repo-before-spaces'")
            ).scalar_one() == "before-spaces"
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(mysql_database_url)
    try:
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT space_id FROM repository WHERE id='repo-before-spaces'")
            ).scalar_one() == "default-space"
    finally:
        engine.dispose()