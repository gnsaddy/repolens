"""Opt-in independent review of investigations

Revision ID: 0023
Revises: 0022
"""

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("llm_settings")}
    if "independent_review" not in cols:
        op.add_column(
            "llm_settings", sa.Column("independent_review", sa.Boolean, nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    op.drop_column("llm_settings", "independent_review")
