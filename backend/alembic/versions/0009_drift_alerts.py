"""Documentation drift alerts

Revision ID: 0009
Revises: 0008
"""

from alembic import op

from repolens import models  # noqa: F401
from repolens.db import Base

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(op.get_bind(), tables=[Base.metadata.tables["drift_alerts"]])


def downgrade() -> None:
    op.drop_table("drift_alerts")
