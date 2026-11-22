"""Investigations: an opt in to let the model look further with read only tools

Revision ID: 0038
Revises: 0037
"""

import sqlalchemy as sa
from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("llm_settings")}
    if "explorer" not in cols:
        op.add_column("llm_settings", sa.Column("explorer", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("llm_settings", "explorer")
