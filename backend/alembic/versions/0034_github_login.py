"""Sign in with GitHub: which e-mail domains may sign in to a workspace

Revision ID: 0034
Revises: 0033
"""

from alembic import op

from repolens import models  # noqa: F401
from repolens.db import Base

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(op.get_bind(), tables=[Base.metadata.tables["github_login_configs"]])


def downgrade() -> None:
    op.drop_table("github_login_configs")
