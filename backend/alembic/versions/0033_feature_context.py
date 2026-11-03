"""Feature drafts remember which other repositories they were written with, and what they were told about them

Revision ID: 0033
Revises: 0032
"""

import sqlalchemy as sa
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("feature_drafts")}
    if "related_ids" not in cols:
        op.add_column("feature_drafts", sa.Column("related_ids", sa.JSON, nullable=False, server_default="[]"))
    if "context" not in cols:
        op.add_column("feature_drafts", sa.Column("context", sa.JSON, nullable=False, server_default="{}"))


def downgrade() -> None:
    op.drop_column("feature_drafts", "context")
    op.drop_column("feature_drafts", "related_ids")
