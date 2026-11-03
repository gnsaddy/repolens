"""Feature drafts can be shared with the workspace; they are private to their author by default

Revision ID: 0031
Revises: 0030
"""

import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("feature_drafts")}
    if "shared" not in cols:
        op.add_column("feature_drafts", sa.Column("shared", sa.Boolean, nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("feature_drafts", "shared")
