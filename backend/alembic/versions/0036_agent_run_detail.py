"""Automations: what a pull request review read and made, on the record of the run

Revision ID: 0036
Revises: 0035
"""

import sqlalchemy as sa
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("agent_runs")}
    if "detail" not in cols:
        op.add_column("agent_runs", sa.Column("detail", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    op.drop_column("agent_runs", "detail")
