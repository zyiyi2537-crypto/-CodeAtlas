"""Add GitLab source configuration.

Revision ID: 20260821_02
Revises: 20260817_01
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260821_02"
down_revision: str | None = "20260817_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "gitlabsource",
        sa.Column("id", sa.String(length=32), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("group_path", sa.String(length=500), nullable=False),
        sa.Column("credential_ref", sa.String(length=200), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("poll_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.String(length=2000), nullable=False),
        sa.Column("created_by", sa.String(length=32), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_gitlabsource_name", "gitlabsource", ["name"])
    op.create_index("ix_gitlabsource_enabled", "gitlabsource", ["enabled"])


def downgrade() -> None:
    op.drop_table("gitlabsource")