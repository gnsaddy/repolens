"""Automations: the inbox of drafts, and the option to review a pull request again after a new commit

Revision ID: 0037
Revises: 0036
"""

import sqlalchemy as sa
from alembic import op

from repolens import models  # noqa: F401
from repolens.db import Base

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind, tables=[Base.metadata.tables["agent_drafts"]])
    cols = {c["name"] for c in sa.inspect(bind).get_columns("agent_rules")}
    if "re_review" not in cols:
        op.add_column("agent_rules", sa.Column("re_review", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("agent_rules", "re_review")
    op.drop_table("agent_drafts")
