"""Saved questions and quality runs

Revision ID: 0025
Revises: 0024
"""

from alembic import op

from repolens import models  # noqa: F401
from repolens.db import Base

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(
        op.get_bind(), tables=[Base.metadata.tables["golden_questions"], Base.metadata.tables["golden_runs"]]
    )


def downgrade() -> None:
    op.drop_table("golden_runs")
    op.drop_table("golden_questions")
