"""GitHub OAuth device flow: connection columns + oauth_flows

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

from repolens import models  # noqa: F401
from repolens.db import Base

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

COLUMNS = {
    "auth_method": sa.Column("auth_method", sa.String(10), nullable=False, server_default="token"),
    "client_id": sa.Column("client_id", sa.String(100), nullable=False, server_default=""),
    "login": sa.Column("login", sa.String(100), nullable=False, server_default=""),
    "scope": sa.Column("scope", sa.String(200), nullable=False, server_default=""),
}


def upgrade() -> None:
    bind = op.get_bind()
    have = {c["name"] for c in sa.inspect(bind).get_columns("github_connections")}
    for name, col in COLUMNS.items():
        if name not in have:
            op.add_column("github_connections", col)
    Base.metadata.create_all(bind, tables=[Base.metadata.tables["oauth_flows"]])


def downgrade() -> None:
    Base.metadata.drop_all(op.get_bind(), tables=[Base.metadata.tables["oauth_flows"]])
    for name in COLUMNS:
        op.drop_column("github_connections", name)
