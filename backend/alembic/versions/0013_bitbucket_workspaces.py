"""Bitbucket Cloud workspaces to list

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("bitbucket_connections")}
    if "workspaces" not in have:
        op.add_column(
            "bitbucket_connections", sa.Column("workspaces", sa.String(300), nullable=False, server_default="")
        )


def downgrade() -> None:
    op.drop_column("bitbucket_connections", "workspaces")
