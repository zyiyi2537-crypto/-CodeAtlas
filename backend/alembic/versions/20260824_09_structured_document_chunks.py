"""Add structural metadata to document chunks.

Revision ID: 20260824_09
Revises: 20260822_08
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260824_09"
down_revision: str | None = "20260822_08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _column_names() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns("documentchunkrecord")}


def _index_names() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {index["name"] for index in inspector.get_indexes("documentchunkrecord")}


def upgrade() -> None:
    columns = _column_names()
    if "structure_type" not in columns:
        op.add_column(
            "documentchunkrecord",
            sa.Column(
                "structure_type",
                sa.String(length=50),
                nullable=False,
                server_default="section",
            ),
        )
    if "metadata_json" not in columns:
        # MySQL 8 rejects DEFAULT values on TEXT columns. Add it nullable,
        # backfill existing rows, then enforce NOT NULL without a default.
        op.add_column(
            "documentchunkrecord",
            sa.Column("metadata_json", sa.Text(), nullable=True),
        )
        op.execute(
            sa.text(
                "UPDATE documentchunkrecord "
                "SET metadata_json = '{}' WHERE metadata_json IS NULL"
            )
        )
        op.alter_column(
            "documentchunkrecord",
            "metadata_json",
            existing_type=sa.Text(),
            nullable=False,
        )
    if "ix_documentchunkrecord_structure_type" not in _index_names():
        op.create_index(
            "ix_documentchunkrecord_structure_type",
            "documentchunkrecord",
            ["structure_type"],
        )


def downgrade() -> None:
    if "ix_documentchunkrecord_structure_type" in _index_names():
        op.drop_index("ix_documentchunkrecord_structure_type", table_name="documentchunkrecord")
    columns = _column_names()
    if "metadata_json" in columns:
        op.drop_column("documentchunkrecord", "metadata_json")
    if "structure_type" in columns:
        op.drop_column("documentchunkrecord", "structure_type")
