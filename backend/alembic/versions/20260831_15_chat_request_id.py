"""Add idempotency keys to persisted chat turns.

Revision ID: 20260831_15
Revises: 20260830_14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260831_15"
down_revision: str | None = "20260830_14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chatsession",
        sa.Column("request_id", sa.String(length=64), nullable=True),
    )
    op.create_unique_constraint(
        "uq_chat_session_request",
        "chatsession",
        ["user_id", "request_id"],
    )
    op.add_column(
        "chatmessage",
        sa.Column("request_id", sa.String(length=64), nullable=True),
    )
    op.create_unique_constraint(
        "uq_chat_message_request",
        "chatmessage",
        ["session_id", "request_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_chat_message_request",
        "chatmessage",
        type_="unique",
    )
    op.drop_column("chatmessage", "request_id")
    op.drop_constraint(
        "uq_chat_session_request",
        "chatsession",
        type_="unique",
    )
    op.drop_column("chatsession", "request_id")
