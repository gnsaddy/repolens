"""Single sign-on (OIDC) and SCIM provisioning

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa
from alembic import op

from repolens import models  # noqa: F401
from repolens.db import Base

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

USER_COLUMNS = [
    sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.Column("auth_source", sa.String(10), nullable=False, server_default="password"),
    sa.Column("external_id", sa.String(200), nullable=False, server_default=""),
]


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("users")}
    for col in USER_COLUMNS:
        if col.name not in have:
            op.add_column("users", col)
    Base.metadata.create_all(
        op.get_bind(), tables=[Base.metadata.tables[t] for t in ("sso_configs", "sso_flows", "scim_tokens")]
    )


def downgrade() -> None:
    for t in ("scim_tokens", "sso_flows", "sso_configs"):
        op.drop_table(t)
    for col in reversed(USER_COLUMNS):
        op.drop_column("users", col.name)
