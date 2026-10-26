"""Organisation skills: guidance for the model, approved by an admin

Revision ID: 0024
Revises: 0023
"""

from alembic import op

from repolens import models  # noqa: F401
from repolens.db import Base

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(op.get_bind(), tables=[Base.metadata.tables["skills"]])


def downgrade() -> None:
    op.drop_table("skills")
