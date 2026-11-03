"""Persona runs: reviews of code by a security auditor or a test engineer

Revision ID: 0032
Revises: 0031
"""

from alembic import op

from repolens import models  # noqa: F401
from repolens.db import Base

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(op.get_bind(), tables=[Base.metadata.tables["persona_runs"]])


def downgrade() -> None:
    op.drop_table("persona_runs")
