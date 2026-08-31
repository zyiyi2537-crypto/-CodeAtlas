"""Add account-scoped chat sessions, messages and persistent memories.

Revision ID: 20260830_14
Revises: 20260829_13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260830_14"
down_revision: str | None = "20260829_13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chatsession",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("repository_ids_json", sa.Text(), nullable=False),
        sa.Column("message_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chatsession_user_id", "chatsession", ["user_id"])
    op.create_index("ix_chatsession_updated_at", "chatsession", ["updated_at"])

    op.create_table(
        "chatmessage",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("session_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("citations_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["chatsession.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id", "sequence", name="uq_chat_message_sequence"
        ),
    )
    op.create_index("ix_chatmessage_session_id", "chatmessage", ["session_id"])
    op.create_index("ix_chatmessage_user_id", "chatmessage", ["user_id"])
    op.create_index("ix_chatmessage_created_at", "chatmessage", ["created_at"])

    op.create_table(
        "usermemory",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "kind", "content_hash", name="uq_user_memory_content"
        ),
    )
    op.create_index("ix_usermemory_user_id", "usermemory", ["user_id"])
    op.create_index("ix_usermemory_kind", "usermemory", ["kind"])
    op.create_index("ix_usermemory_updated_at", "usermemory", ["updated_at"])


def downgrade() -> None:
    op.drop_table("chatmessage")
    op.drop_table("chatsession")
    op.drop_table("usermemory")
