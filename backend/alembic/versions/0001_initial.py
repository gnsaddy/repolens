"""initial schema: all tables, pgvector extension, FTS + HNSW indexes

Revision ID: 0001
Revises:
"""

from alembic import op

from repolens import models  # noqa: F401
from repolens.db import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    Base.metadata.create_all(bind)
    if bind.dialect.name == "postgresql":
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_code_chunks_fts ON code_chunks USING gin (to_tsvector('simple', content))"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_code_chunks_vec ON code_chunks USING hnsw (embedding vector_cosine_ops)"
        )


def downgrade() -> None:
    Base.metadata.drop_all(op.get_bind())
