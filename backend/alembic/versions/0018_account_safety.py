"""Authenticator-app sign-in, invitations and password-reset links

Revision ID: 0018
Revises: 0017
"""

import sqlalchemy as sa
from alembic import op

from repolens import models  # noqa: F401
from repolens.db import Base

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    have = {c["name"] for c in sa.inspect(bind).get_columns("users")}
    for name, col in (
        ("totp_secret_encrypted", sa.Column("totp_secret_encrypted", sa.String(300), nullable=False, server_default="")),
        ("totp_enabled", sa.Column("totp_enabled", sa.Boolean, nullable=False, server_default=sa.false())),
        ("totp_last_step", sa.Column("totp_last_step", sa.Integer, nullable=False, server_default="0")),
        ("recovery_codes", sa.Column("recovery_codes", sa.JSON, nullable=False, server_default="[]")),
    ):
        if name not in have:
            op.add_column("users", col)
    Base.metadata.create_all(bind, tables=[Base.metadata.tables["auth_tokens"]])


def downgrade() -> None:
    op.drop_table("auth_tokens")
    for name in ("recovery_codes", "totp_last_step", "totp_enabled", "totp_secret_encrypted"):
        op.drop_column("users", name)
