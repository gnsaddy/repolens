"""risk assessments (Oracle)

Revision ID: 0004
Revises: 0003
"""

from alembic import op

from repolens import models  # noqa: F401
from repolens.db import Base

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(op.get_bind(), tables=[Base.metadata.tables["risk_assessments"]])


def downgrade() -> None:
    Base.metadata.drop_all(op.get_bind(), tables=[Base.metadata.tables["risk_assessments"]])
