"""Pluggable sources: per-source config, encrypted secret and sync schedule

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

COLUMNS = [
    sa.Column("config", sa.JSON, nullable=False, server_default="{}"),
    sa.Column("secret_encrypted", sa.Text, nullable=False, server_default=""),
    sa.Column("sync_interval_hours", sa.Integer, nullable=False, server_default="0"),
    sa.Column("next_sync_at", sa.DateTime(timezone=True), nullable=True),
]


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("wiki_sources")}
    for col in COLUMNS:
        if col.name not in have:
            op.add_column("wiki_sources", col)


def downgrade() -> None:
    for col in reversed(COLUMNS):
        op.drop_column("wiki_sources", col.name)
