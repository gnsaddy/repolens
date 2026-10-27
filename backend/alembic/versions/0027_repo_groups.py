"""Repository groups (systems) and their written overviews

Revision ID: 0027
Revises: 0026
"""

from alembic import op

from repolens import models  # noqa: F401
from repolens.db import Base

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(
        op.get_bind(), tables=[Base.metadata.tables["repo_groups"], Base.metadata.tables["group_overviews"]]
    )


def downgrade() -> None:
    op.drop_table("group_overviews")
    op.drop_table("repo_groups")
