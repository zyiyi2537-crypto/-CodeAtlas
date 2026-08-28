"""Add encrypted browser-managed embedding credentials.

Revision ID: 20260827_12
Revises: 20260825_11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260827_12"
down_revision: str | None = "20260825_11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "embeddingprofile",
        sa.Column(
            "api_key_ciphertext",
            sa.Text(),
            nullable=True,
        ),
    )
    op.execute(
        sa.text(
            "UPDATE embeddingprofile "
            "SET api_key_ciphertext = '' "
            "WHERE api_key_ciphertext IS NULL"
        )
    )
    op.alter_column(
        "embeddingprofile",
        "api_key_ciphertext",
        existing_type=sa.Text(),
        nullable=False,
    )


def downgrade() -> None:
    op.drop_column("embeddingprofile", "api_key_ciphertext")
