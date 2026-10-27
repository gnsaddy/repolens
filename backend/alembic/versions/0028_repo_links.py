"""Stored dependencies between repositories

Revision ID: 0028
Revises: 0027
"""

from alembic import op

from repolens import models  # noqa: F401
from repolens.db import Base

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(
        op.get_bind(), tables=[Base.metadata.tables["repo_links"], Base.metadata.tables["link_states"]]
    )


def downgrade() -> None:
    op.drop_table("link_states")
    op.drop_table("repo_links")
