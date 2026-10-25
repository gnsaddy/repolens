"""Feedback on answers and investigations

Revision ID: 0017
Revises: 0016
"""

from alembic import op

from repolens import models  # noqa: F401
from repolens.db import Base

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(op.get_bind(), tables=[Base.metadata.tables["feedback_events"]])


def downgrade() -> None:
    op.drop_table("feedback_events")
