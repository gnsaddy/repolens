"""Server-side user sessions

Revision ID: 0016
Revises: 0015
"""

from alembic import op

from repolens import models  # noqa: F401
from repolens.db import Base

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(op.get_bind(), tables=[Base.metadata.tables["user_sessions"]])


def downgrade() -> None:
    op.drop_table("user_sessions")
