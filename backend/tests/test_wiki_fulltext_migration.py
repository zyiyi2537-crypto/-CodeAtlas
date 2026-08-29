from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command


def wiki_fulltext_columns(engine) -> list[tuple[str, str]]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT COLUMN_NAME, INDEX_TYPE "
                "FROM information_schema.statistics "
                "WHERE table_schema = DATABASE() AND table_name = 'wikipage' "
                "AND index_name = 'ft_wikipage_search' "
                "ORDER BY SEQ_IN_INDEX"
            )
        ).all()
    return [(str(row[0]), str(row[1]).upper()) for row in rows]


def wiki_uses_ngram_parser(engine) -> bool:
    with engine.connect() as connection:
        create_table = str(connection.execute(text("SHOW CREATE TABLE wikipage")).one()[1])
    normalized = " ".join(create_table.lower().split())
    return (
        "fulltext key `ft_wikipage_search`" in normalized
        and "with parser `ngram`" in normalized
    )


def test_wiki_fulltext_migration_repairs_and_downgrades_on_mysql(
    mysql_database_url: str, monkeypatch
) -> None:
    monkeypatch.setenv("CODEATLAS_DATABASE_URL", mysql_database_url)
    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / "alembic.ini"))

    command.upgrade(config, "20260827_12")
    engine = create_engine(mysql_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP INDEX ft_wikipage_search ON wikipage"))
            connection.execute(
                text(
                    "CREATE FULLTEXT INDEX ft_wikipage_search "
                    "ON wikipage (title, content)"
                )
            )
        assert wiki_fulltext_columns(engine) == [
            ("title", "FULLTEXT"),
            ("content", "FULLTEXT"),
        ]
        assert not wiki_uses_ngram_parser(engine)
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(mysql_database_url)
    try:
        assert wiki_fulltext_columns(engine) == [
            ("title", "FULLTEXT"),
            ("content", "FULLTEXT"),
        ]
        assert wiki_uses_ngram_parser(engine)
    finally:
        engine.dispose()

    command.downgrade(config, "20260827_12")
    engine = create_engine(mysql_database_url)
    try:
        assert wiki_fulltext_columns(engine) == [
            ("title", "FULLTEXT"),
            ("content", "FULLTEXT"),
        ]
        assert wiki_uses_ngram_parser(engine)
    finally:
        engine.dispose()
