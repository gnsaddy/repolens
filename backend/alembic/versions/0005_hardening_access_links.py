"""repository access control, evidence links, secret-redaction counter

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

from repolens import models  # noqa: F401
from repolens.db import Base

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

NEW_COLUMNS = {
    "repositories": [
        sa.Column("visibility", sa.String(12), nullable=False, server_default="org"),
        sa.Column("web_url", sa.String(500), nullable=False, server_default=""),
    ],
    "index_runs": [sa.Column("secrets_redacted", sa.Integer, nullable=False, server_default="0")],
    "memory_entries": [sa.Column("scope_repo_ids", sa.JSON, nullable=True)],
}


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    for table, cols in NEW_COLUMNS.items():
        have = {c["name"] for c in insp.get_columns(table)}
        for col in cols:
            if col.name not in have:
                op.add_column(table, col)
    Base.metadata.create_all(bind, tables=[Base.metadata.tables["repo_access"]])


def downgrade() -> None:
    Base.metadata.drop_all(op.get_bind(), tables=[Base.metadata.tables["repo_access"]])
    for table, cols in NEW_COLUMNS.items():
        for col in cols:
            op.drop_column(table, col.name)
