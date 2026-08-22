"""Add source-tracked Wiki pages."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260821_05"
down_revision: str | None = "20260821_04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wikipage",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("path", sa.String(500), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sources_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("created_by", sa.String(32), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_wikipage_status", "wikipage", ["status"])
    op.create_index(
        "ft_wikipage_search", "wikipage", ["title", "content"],
        mysql_prefix="FULLTEXT", mysql_with_parser="ngram",
    )


def downgrade() -> None:
    op.drop_table("wikipage")
