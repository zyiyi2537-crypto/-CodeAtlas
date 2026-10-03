"""Add optional path scopes to GitHub sources.

Revision ID: 20261003_17
Revises: 20260904_16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261003_17"
down_revision: str | None = "20260904_16"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "githubsource",
        sa.Column(
            "include_paths_json",
            sa.Text(),
            nullable=True,
        ),
    )
    op.add_column(
        "githubsource",
        sa.Column("pinned_commit", sa.String(length=40), nullable=False, server_default=""),
    )
    op.execute("UPDATE githubsource SET include_paths_json = '[]' WHERE include_paths_json IS NULL")
    op.alter_column(
        "githubsource",
        "include_paths_json",
        existing_type=sa.Text(),
        nullable=False,
    )


def downgrade() -> None:
    op.drop_column("githubsource", "pinned_commit")
    op.drop_column("githubsource", "include_paths_json")
