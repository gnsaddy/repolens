"""Multi-provider model registry, routing mode and local answer memory

Revision ID: 0015
Revises: 0014
"""

import sqlalchemy as sa
from alembic import op

from repolens import models  # noqa: F401
from repolens.db import Base

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "savings_mode" not in {c["name"] for c in insp.get_columns("llm_settings")}:
        op.add_column("llm_settings", sa.Column("savings_mode", sa.Boolean, nullable=False, server_default=sa.true()))
    Base.metadata.create_all(
        bind, tables=[Base.metadata.tables[t] for t in ("llm_connections", "llm_models", "answer_memory")]
    )


def downgrade() -> None:
    for t in ("answer_memory", "llm_models", "llm_connections"):
        op.drop_table(t)
    op.drop_column("llm_settings", "savings_mode")
