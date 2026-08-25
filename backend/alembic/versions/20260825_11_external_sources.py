"""Add external document source configuration and item tracking.

Revision ID: 20260825_11
Revises: 20260824_10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260825_11"
down_revision: str | None = "20260824_10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "externalsource",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("collection_id", sa.String(length=32), nullable=False),
        sa.Column("credential_ref", sa.String(length=200), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("poll_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("sync_status", sa.String(length=30), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.String(length=2000), nullable=False),
        sa.Column("last_result_json", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["collection_id"], ["documentcollection.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_externalsource_collection_id", "externalsource", ["collection_id"])
    op.create_index("ix_externalsource_created_at", "externalsource", ["created_at"])
    op.create_index("ix_externalsource_enabled", "externalsource", ["enabled"])
    op.create_index("ix_externalsource_name", "externalsource", ["name"])
    op.create_index("ix_externalsource_provider", "externalsource", ["provider"])
    op.create_index("ix_externalsource_sync_status", "externalsource", ["sync_status"])

    op.create_table(
        "externalsourceitem",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("external_id_hash", sa.String(length=64), nullable=False),
        sa.Column("document_id", sa.String(length=32), nullable=True),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("mime_type", sa.String(length=120), nullable=False),
        sa.Column("revision", sa.String(length=500), nullable=False),
        sa.Column("modified_at", sa.String(length=100), nullable=False),
        sa.Column("source_url", sa.String(length=2000), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["document.id"]),
        sa.ForeignKeyConstraint(["source_id"], ["externalsource.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "external_id_hash"),
    )
    op.create_index("ix_externalsourceitem_document_id", "externalsourceitem", ["document_id"])
    op.create_index("ix_externalsourceitem_source_id", "externalsourceitem", ["source_id"])


def downgrade() -> None:
    op.drop_table("externalsourceitem")
    op.drop_table("externalsource")
