"""Link repositories to GitLab projects.

Revision ID: 20260821_03
Revises: 20260821_02
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260821_03"
down_revision: str | None = "20260821_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("repository", sa.Column("source_id", sa.String(32), nullable=True))
    op.add_column("repository", sa.Column("external_project_id", sa.String(100), nullable=True))
    op.create_index("ix_repository_source_id", "repository", ["source_id"])
    op.create_index("ix_repository_external_project_id", "repository", ["external_project_id"])
    op.create_foreign_key(
        "fk_repository_source_id_gitlabsource",
        "repository",
        "gitlabsource",
        ["source_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_repository_source_id_gitlabsource", "repository", type_="foreignkey")
    op.drop_index("ix_repository_external_project_id", table_name="repository")
    op.drop_index("ix_repository_source_id", table_name="repository")
    op.drop_column("repository", "external_project_id")
    op.drop_column("repository", "source_id")
