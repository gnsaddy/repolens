"""Remember which account each integration credential belongs to

Revision ID: 0014
Revises: 0013
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None
TABLES = ("jira_connections", "confluence_connections", "bitbucket_connections")


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    for t in TABLES:
        if "account" not in {c["name"] for c in insp.get_columns(t)}:
            op.add_column(t, sa.Column("account", sa.String(200), nullable=False, server_default=""))


def downgrade() -> None:
    for t in TABLES:
        op.drop_column(t, "account")
