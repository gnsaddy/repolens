"""Wiki edit proposals

Revision ID: 0010
Revises: 0009
"""

from alembic import op

from repolens import models  # noqa: F401
from repolens.db import Base

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(op.get_bind(), tables=[Base.metadata.tables["doc_proposals"]])


def downgrade() -> None:
    op.drop_table("doc_proposals")
