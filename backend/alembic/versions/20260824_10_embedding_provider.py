"""Add embedding provider protocol.

Revision ID: 20260824_10
Revises: 20260824_09
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260824_10"
down_revision: str | None = "20260824_09"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "embeddingprofile",
        sa.Column(
            "provider",
            sa.String(length=40),
            nullable=False,
            server_default="openai",
        ),
    )


def downgrade() -> None:
    op.drop_column("embeddingprofile", "provider")
