"""Evaluation runs on a person's own repository

Revision ID: 0022
Revises: 0021
"""

from alembic import op

from repolens import models  # noqa: F401
from repolens.db import Base

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(op.get_bind(), tables=[Base.metadata.tables["eval_runs"]])


def downgrade() -> None:
    op.drop_table("eval_runs")
