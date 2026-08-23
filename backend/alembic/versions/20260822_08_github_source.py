"""Add GitHub SSH source configuration.

Revision ID: 20260822_08
Revises: 20260822_07
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260822_08"
down_revision: str | None = "20260822_07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "githubsource",
        sa.Column("id", sa.String(length=32), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("repo_url", sa.String(length=500), nullable=False),
        sa.Column("owner", sa.String(length=100), nullable=False),
        sa.Column("repository", sa.String(length=100), nullable=False),
        sa.Column("branch", sa.String(length=200), nullable=False),
        sa.Column("credential_ref", sa.String(length=200), nullable=False),
        sa.Column("repository_id", sa.String(length=32), nullable=False),
        sa.Column("ssh_key_path", sa.String(length=1000), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("poll_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.String(length=2000), nullable=False),
        sa.Column("created_by", sa.String(length=32), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["repository_id"], ["repository.id"]),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("repository_id"),
    )
    op.create_index("ix_githubsource_name", "githubsource", ["name"])
    op.create_index("ix_githubsource_repository_id", "githubsource", ["repository_id"])
    op.create_index("ix_githubsource_enabled", "githubsource", ["enabled"])


def downgrade() -> None:
    op.drop_table("githubsource")
