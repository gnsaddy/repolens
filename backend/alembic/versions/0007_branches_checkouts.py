"""Per-branch indexes and checkout usage tracking

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("repositories")}
    if "branch" not in have:
        op.add_column("repositories", sa.Column("branch", sa.String(200), nullable=False, server_default=""))
    if "last_used_at" not in have:
        op.add_column("repositories", sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("repositories", "last_used_at")
    op.drop_column("repositories", "branch")
