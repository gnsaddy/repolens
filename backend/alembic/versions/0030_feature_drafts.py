"""Feature drafts

Revision ID: 0030
Revises: 0029
"""

from alembic import op

from repolens import models  # noqa: F401
from repolens.db import Base

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(op.get_bind(), tables=[Base.metadata.tables["feature_drafts"]])


def downgrade() -> None:
    op.drop_table("feature_drafts")
