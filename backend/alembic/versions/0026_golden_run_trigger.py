"""Saved-question runs remember what started them

Revision ID: 0026
Revises: 0025
"""

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("golden_runs")}
    if "trigger" not in cols:
        op.add_column("golden_runs", sa.Column("trigger", sa.String(20), nullable=False, server_default="manual"))


def downgrade() -> None:
    op.drop_column("golden_runs", "trigger")
