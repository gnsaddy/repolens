"""Automations: a pause switch and a daily token budget for the agent

Revision ID: 0039
Revises: 0038
"""

from alembic import op

from repolens import models  # noqa: F401
from repolens.db import Base

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(op.get_bind(), tables=[Base.metadata.tables["agent_settings"]])


def downgrade() -> None:
    op.drop_table("agent_settings")
