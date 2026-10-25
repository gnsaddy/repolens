"""Personal access tokens

Revision ID: 0019
Revises: 0018
"""

from alembic import op

from repolens import models  # noqa: F401
from repolens.db import Base

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(op.get_bind(), tables=[Base.metadata.tables["api_tokens"]])


def downgrade() -> None:
    op.drop_table("api_tokens")
