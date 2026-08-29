"""Ensure Wiki pages have a matching MySQL FULLTEXT index.

Revision ID: 20260829_13
Revises: 20260827_12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260829_13"
down_revision: str | None = "20260827_12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "ft_wikipage_search"
EXPECTED_COLUMNS = ["title", "content"]


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "wikipage" not in inspector.get_table_names():
        return
    existing = {index["name"] for index in inspector.get_indexes("wikipage")}
    # information_schema.statistics exposes columns and FULLTEXT type but not
    # the parser plugin. Rebuild the named index once in this migration so a
    # same-name default-parser index is repaired for Chinese ngram retrieval.
    if INDEX_NAME in existing:
        op.drop_index(INDEX_NAME, table_name="wikipage")
    op.create_index(
        INDEX_NAME,
        "wikipage",
        EXPECTED_COLUMNS,
        mysql_prefix="FULLTEXT",
        mysql_with_parser="ngram",
    )


def downgrade() -> None:
    # Revision 20260821_05 already defines this index. Revision 13 repairs
    # databases where it is missing or malformed, so downgrading to revision
    # 12 must preserve the schema that revision 05 intended.
    return