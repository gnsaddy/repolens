"""documents, knowledge graph, LLM settings, GitHub connection, pull requests

Revision ID: 0002
Revises: 0001
"""

from alembic import op

from repolens import models  # noqa: F401
from repolens.db import Base

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

NEW_TABLES = ["documents", "kg_nodes", "kg_edges", "llm_settings", "github_connections", "pull_requests"]


def upgrade() -> None:
    # create_all only creates the tables that do not exist yet, so databases created by 0001 on an older
    # model set gain exactly the new tables.
    Base.metadata.create_all(op.get_bind(), tables=[Base.metadata.tables[t] for t in NEW_TABLES])


def downgrade() -> None:
    Base.metadata.drop_all(op.get_bind(), tables=[Base.metadata.tables[t] for t in reversed(NEW_TABLES)])
