"""Bitbucket connection

Revision ID: 0012
Revises: 0011
"""

from alembic import op

from repolens import models  # noqa: F401
from repolens.db import Base

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(op.get_bind(), tables=[Base.metadata.tables["bitbucket_connections"]])


def downgrade() -> None:
    op.drop_table("bitbucket_connections")
