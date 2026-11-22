"""Webhook deliveries: what GitHub sent and what RepoLens did with it, for checking the connection

Revision ID: 0040
Revises: 0039
"""

from alembic import op

from repolens import models  # noqa: F401
from repolens.db import Base

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(op.get_bind(), tables=[Base.metadata.tables["webhook_deliveries"]])


def downgrade() -> None:
    op.drop_table("webhook_deliveries")
