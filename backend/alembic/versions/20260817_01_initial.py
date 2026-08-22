"""Create the initial MySQL-backed CodeAtlas schema.

Revision ID: 20260817_01
Revises:
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260817_01"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def identifier() -> sa.Column:
    return sa.Column("id", sa.String(length=32), primary_key=True, nullable=False)


def timestamp(name: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(), nullable=nullable)


def foreign_id(name: str, target: str) -> sa.Column:
    return sa.Column(name, sa.String(length=32), sa.ForeignKey(target), nullable=False)


def upgrade() -> None:
    op.create_table(
        "user",
        identifier(),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        timestamp("created_at"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_user_email", "user", ["email"])
    op.create_index("ix_user_role", "user", ["role"])
    op.create_index("ix_user_is_active", "user", ["is_active"])

    op.create_table(
        "usersession",
        identifier(),
        foreign_id("user_id", "user.id"),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_token", sa.String(length=100), nullable=False),
        timestamp("expires_at"),
        timestamp("created_at"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_usersession_user_id", "usersession", ["user_id"])
    op.create_index("ix_usersession_token_hash", "usersession", ["token_hash"])
    op.create_index("ix_usersession_expires_at", "usersession", ["expires_at"])

    op.create_table(
        "repository",
        identifier(),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("git_url", sa.String(length=1000), nullable=False),
        sa.Column("branch", sa.String(length=200), nullable=False),
        sa.Column("visibility", sa.String(length=20), nullable=False),
        sa.Column("license_name", sa.String(length=100), nullable=False),
        sa.Column("license_url", sa.String(length=1000), nullable=False),
        sa.Column("local_path", sa.String(length=1000), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("active_generation_id", sa.String(length=32), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("last_commit", sa.String(length=64), nullable=False),
        timestamp("last_indexed_at", nullable=True),
        foreign_id("created_by", "user.id"),
        timestamp("created_at"),
        sa.UniqueConstraint("name"),
    )
    for column in ("name", "visibility", "status", "active_generation_id"):
        op.create_index(f"ix_repository_{column}", "repository", [column])

    op.create_table(
        "repositoryaccess",
        identifier(),
        foreign_id("repository_id", "repository.id"),
        foreign_id("user_id", "user.id"),
        timestamp("created_at"),
        sa.UniqueConstraint("repository_id", "user_id", name="uq_repository_access"),
    )
    op.create_index(
        "ix_repositoryaccess_repository_id", "repositoryaccess", ["repository_id"]
    )
    op.create_index("ix_repositoryaccess_user_id", "repositoryaccess", ["user_id"])

    op.create_table(
        "apitoken",
        identifier(),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("token_prefix", sa.String(length=16), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("scopes_json", sa.Text(), nullable=False),
        sa.Column("repository_ids_json", sa.Text(), nullable=False),
        foreign_id("created_by", "user.id"),
        timestamp("expires_at", nullable=True),
        timestamp("revoked_at", nullable=True),
        timestamp("created_at"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_apitoken_token_prefix", "apitoken", ["token_prefix"])
    op.create_index("ix_apitoken_token_hash", "apitoken", ["token_hash"])

    op.create_table(
        "indexjob",
        identifier(),
        foreign_id("repository_id", "repository.id"),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("error", sa.String(length=2000), nullable=False),
        sa.Column("commit", sa.String(length=64), nullable=False),
        sa.Column("generation_id", sa.String(length=64), nullable=False),
        foreign_id("created_by", "user.id"),
        timestamp("created_at"),
        timestamp("started_at", nullable=True),
        timestamp("finished_at", nullable=True),
    )
    op.create_index("ix_indexjob_repository_id", "indexjob", ["repository_id"])
    op.create_index("ix_indexjob_status", "indexjob", ["status"])

    op.create_table(
        "indexgeneration",
        identifier(),
        foreign_id("repository_id", "repository.id"),
        sa.Column("commit", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        timestamp("created_at"),
        timestamp("activated_at", nullable=True),
    )
    op.create_index(
        "ix_indexgeneration_repository_id", "indexgeneration", ["repository_id"]
    )
    op.create_index("ix_indexgeneration_status", "indexgeneration", ["status"])

    op.create_table(
        "codechunkrecord",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        foreign_id("generation_id", "indexgeneration.id"),
        foreign_id("repository_id", "repository.id"),
        sa.Column("commit", sa.String(length=64), nullable=False),
        sa.Column("path", sa.String(length=1000), nullable=False),
        sa.Column("language", sa.String(length=50), nullable=False),
        sa.Column("symbol", sa.String(length=500), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
    )
    for column in ("generation_id", "repository_id", "language"):
        op.create_index(f"ix_codechunkrecord_{column}", "codechunkrecord", [column])
    op.create_index(
        "ft_codechunkrecord_search",
        "codechunkrecord",
        ["path", "symbol", "content"],
        mysql_prefix="FULLTEXT",
        mysql_with_parser="ngram",
    )

    op.create_table(
        "auditevent",
        identifier(),
        sa.Column("actor_user_id", sa.String(length=32), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("target_type", sa.String(length=50), nullable=False),
        sa.Column("target_id", sa.String(length=100), nullable=False),
        sa.Column("detail_json", sa.Text(), nullable=False),
        timestamp("created_at"),
    )
    op.create_index("ix_auditevent_actor_user_id", "auditevent", ["actor_user_id"])
    op.create_index("ix_auditevent_action", "auditevent", ["action"])
    op.create_index("ix_auditevent_created_at", "auditevent", ["created_at"])


def downgrade() -> None:
    for table in (
        "auditevent",
        "codechunkrecord",
        "indexgeneration",
        "indexjob",
        "apitoken",
        "repositoryaccess",
        "repository",
        "usersession",
        "user",
    ):
        op.drop_table(table)
