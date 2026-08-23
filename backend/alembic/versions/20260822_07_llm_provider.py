"""Add persisted OpenAI-compatible LLM providers."""

import sqlalchemy as sa

from alembic import op

revision = "20260822_07"
down_revision = "20260821_06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llmprovider",
        sa.Column("id", sa.String(length=32), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("api_key_ciphertext", sa.Text(), nullable=False),
        sa.Column("models_json", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.String(length=32), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_llmprovider_is_active", "llmprovider", ["is_active"])


def downgrade() -> None:
    op.drop_table("llmprovider")