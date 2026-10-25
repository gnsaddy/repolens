"""Drafted code fixes that wait for a person's approval

Revision ID: 0020
Revises: 0019
"""

from alembic import op

from repolens import models  # noqa: F401
from repolens.db import Base

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(op.get_bind(), tables=[Base.metadata.tables["fix_proposals"]])


def downgrade() -> None:
    op.drop_table("fix_proposals")
