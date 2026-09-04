"""Add the default knowledge space and unified authorization metadata.

Revision ID: 20260904_16
Revises: 20260831_15
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision: str = "20260904_16"
down_revision: str | None = "20260831_15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_WORKSPACE_ID = "default-workspace"
DEFAULT_SPACE_ID = "default-space"


def _space_column() -> sa.Column:
    return sa.Column("space_id", sa.String(length=32), nullable=True)


def _add_space_reference(table: str) -> None:
    op.add_column(table, _space_column())
    op.execute(
        sa.text(f"UPDATE {table} SET space_id = :space_id").bindparams(
            space_id=DEFAULT_SPACE_ID
        )
    )
    op.alter_column(table, "space_id", existing_type=sa.String(length=32), nullable=False)
    op.create_index(f"ix_{table}_space_id", table, ["space_id"])
    op.create_foreign_key(
        f"fk_{table}_space_id",
        table,
        "knowledgespace",
        ["space_id"],
        ["id"],
    )


def upgrade() -> None:
    op.create_table(
        "workspace",
        sa.Column("id", sa.String(length=32), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "knowledgespace",
        sa.Column("id", sa.String(length=32), primary_key=True, nullable=False),
        sa.Column(
            "workspace_id",
            sa.String(length=32),
            sa.ForeignKey("workspace.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("visibility", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "workspace_id", "name", name="uq_knowledge_space_name"
        ),
    )
    op.create_index(
        "ix_knowledgespace_workspace_id", "knowledgespace", ["workspace_id"]
    )
    op.create_index(
        "ix_knowledgespace_visibility", "knowledgespace", ["visibility"]
    )

    now = datetime.now(UTC).replace(tzinfo=None)
    workspace_table = sa.table(
        "workspace",
        sa.column("id", sa.String()),
        sa.column("name", sa.String()),
        sa.column("created_at", sa.DateTime()),
    )
    space_table = sa.table(
        "knowledgespace",
        sa.column("id", sa.String()),
        sa.column("workspace_id", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.String()),
        sa.column("visibility", sa.String()),
        sa.column("created_at", sa.DateTime()),
    )
    op.bulk_insert(
        workspace_table,
        [{"id": DEFAULT_WORKSPACE_ID, "name": "CodeAtlas", "created_at": now}],
    )
    op.bulk_insert(
        space_table,
        [
            {
                "id": DEFAULT_SPACE_ID,
                "workspace_id": DEFAULT_WORKSPACE_ID,
                "name": "Default",
                "description": "Default knowledge space",
                "visibility": "workspace",
                "created_at": now,
            }
        ],
    )

    op.create_table(
        "spacegrant",
        sa.Column("id", sa.String(length=32), primary_key=True, nullable=False),
        sa.Column(
            "space_id",
            sa.String(length=32),
            sa.ForeignKey("knowledgespace.id"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(length=32),
            sa.ForeignKey("user.id"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("space_id", "user_id", name="uq_space_grant"),
    )
    op.create_index("ix_spacegrant_space_id", "spacegrant", ["space_id"])
    op.create_index("ix_spacegrant_user_id", "spacegrant", ["user_id"])
    op.create_index("ix_spacegrant_role", "spacegrant", ["role"])

    for table in (
        "repository",
        "documentcollection",
        "documentchunkrecord",
        "wikipage",
        "codechunkrecord",
    ):
        _add_space_reference(table)

    op.create_unique_constraint(
        "uq_wiki_page_space_path", "wikipage", ["space_id", "path"]
    )

    op.add_column("apitoken", sa.Column("space_ids_json", sa.Text(), nullable=True))
    op.execute(
        sa.text("UPDATE apitoken SET space_ids_json = :space_ids").bindparams(
            space_ids='["default-space"]'
        )
    )
    op.alter_column("apitoken", "space_ids_json", existing_type=sa.Text(), nullable=False)

    op.create_table(
        "companyconvention",
        sa.Column("id", sa.String(length=32), primary_key=True, nullable=False),
        sa.Column(
            "space_id",
            sa.String(length=32),
            sa.ForeignKey("knowledgespace.id"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("language", sa.String(length=50), nullable=False),
        sa.Column("framework", sa.String(length=100), nullable=False),
        sa.Column("task", sa.String(length=200), nullable=False),
        sa.Column("rule", sa.Text(), nullable=False),
        sa.Column("prohibited_pattern", sa.Text(), nullable=False),
        sa.Column("examples_json", sa.Text(), nullable=False),
        sa.Column("citations_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "created_by",
            sa.String(length=32),
            sa.ForeignKey("user.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    for column in ("space_id", "category", "language", "framework", "status", "updated_at"):
        op.create_index(
            f"ix_companyconvention_{column}", "companyconvention", [column]
        )


def downgrade() -> None:
    op.drop_table("companyconvention")
    op.drop_column("apitoken", "space_ids_json")
    op.drop_constraint("uq_wiki_page_space_path", "wikipage", type_="unique")
    for table in (
        "codechunkrecord",
        "wikipage",
        "documentchunkrecord",
        "documentcollection",
        "repository",
    ):
        op.drop_constraint(f"fk_{table}_space_id", table, type_="foreignkey")
        op.drop_index(f"ix_{table}_space_id", table_name=table)
        op.drop_column(table, "space_id")
    op.drop_table("spacegrant")
    op.drop_table("knowledgespace")
    op.drop_table("workspace")
