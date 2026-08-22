"""Add document collections and searchable document chunks."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260821_04"
down_revision: str | None = "20260821_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "documentcollection",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("created_by", sa.String(32), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "document",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "collection_id", sa.String(32), sa.ForeignKey("documentcollection.id"), nullable=False
        ),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("original_filename", sa.String(500), nullable=False),
        sa.Column("mime_type", sa.String(120), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source_path", sa.String(1000), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(32), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_document_collection_id", "document", ["collection_id"])
    op.create_index("ix_document_status", "document", ["status"])
    op.create_table(
        "documentchunkrecord",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "document_id", sa.String(32), sa.ForeignKey("document.id"), nullable=False
        ),
        sa.Column(
            "collection_id", sa.String(32), sa.ForeignKey("documentcollection.id"), nullable=False
        ),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("section", sa.String(500), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_documentchunkrecord_document_id", "documentchunkrecord", ["document_id"]
    )
    op.create_index(
        "ix_documentchunkrecord_collection_id", "documentchunkrecord", ["collection_id"]
    )
    op.create_index(
        "ft_documentchunk_search", "documentchunkrecord", ["title", "section", "content"],
        mysql_prefix="FULLTEXT", mysql_with_parser="ngram",
    )


def downgrade() -> None:
    op.drop_table("documentchunkrecord")
    op.drop_table("document")
    op.drop_table("documentcollection")
