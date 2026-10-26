"""Private chat threads with the knowledge base

Revision ID: 0021
Revises: 0020
"""

from alembic import op

from repolens import models  # noqa: F401
from repolens.db import Base

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(
        op.get_bind(), tables=[Base.metadata.tables["chat_threads"], Base.metadata.tables["chat_messages"]]
    )


def downgrade() -> None:
    op.drop_table("chat_messages")
    op.drop_table("chat_threads")
