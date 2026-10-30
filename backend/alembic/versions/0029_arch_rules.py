"""Architecture rules of a saved system

Revision ID: 0029
Revises: 0028
"""

from alembic import op

from repolens import models  # noqa: F401
from repolens.db import Base

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(op.get_bind(), tables=[Base.metadata.tables["arch_rules"]])


def downgrade() -> None:
    op.drop_table("arch_rules")
