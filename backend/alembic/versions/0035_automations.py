"""Automations: rules that start an investigation when a CI run fails, and a record of each time they did

Revision ID: 0035
Revises: 0034
"""

import sqlalchemy as sa
from alembic import op

from repolens import models  # noqa: F401
from repolens.db import Base

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind, tables=[Base.metadata.tables["agent_rules"], Base.metadata.tables["agent_runs"]])
    cols = {c["name"] for c in sa.inspect(bind).get_columns("investigations")}
    if "source" not in cols:
        op.add_column("investigations", sa.Column("source", sa.String(20), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("investigations", "source")
    op.drop_table("agent_runs")
    op.drop_table("agent_rules")
