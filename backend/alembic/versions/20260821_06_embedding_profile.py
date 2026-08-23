"""Add embedding profile configuration."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260821_06"
down_revision: str | None = "20260821_05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "embeddingprofile",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("base_url", sa.String(500), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("credential_ref", sa.String(200), nullable=False),
        sa.Column("backend", sa.String(30), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(32), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_embeddingprofile_is_active", "embeddingprofile", ["is_active"])


def downgrade() -> None:
    op.drop_table("embeddingprofile")
