"""DeepMind wiki: document source metadata, Confluence connection, wiki sources

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

from repolens import models  # noqa: F401
from repolens.db import Base

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

DOC_COLUMNS = [
    sa.Column("source_type", sa.String(20), nullable=False, server_default="upload"),
    sa.Column("source_url", sa.String(1000), nullable=False, server_default=""),
    sa.Column("source_id", sa.String(100), nullable=False, server_default=""),
    sa.Column("source_version", sa.Integer, nullable=False, server_default="0"),
    sa.Column("parent_path", sa.String(1000), nullable=False, server_default=""),
    sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
]
NEW_TABLES = ["confluence_connections", "wiki_sources"]


def upgrade() -> None:
    bind = op.get_bind()
    have = {c["name"] for c in sa.inspect(bind).get_columns("documents")}
    for col in DOC_COLUMNS:
        if col.name not in have:
            op.add_column("documents", col)
    op.create_index("ix_documents_source_id", "documents", ["source_id"], if_not_exists=True)
    Base.metadata.create_all(bind, tables=[Base.metadata.tables[t] for t in NEW_TABLES])


def downgrade() -> None:
    Base.metadata.drop_all(op.get_bind(), tables=[Base.metadata.tables[t] for t in reversed(NEW_TABLES)])
    op.drop_index("ix_documents_source_id", "documents")
    for col in DOC_COLUMNS:
        op.drop_column("documents", col.name)
