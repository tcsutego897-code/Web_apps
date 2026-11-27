"""Add administrator accounts and invitation tokens.

Revision ID: 0005_admin_invitations
Revises: 0004_account_owned_data
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_admin_invitations"
down_revision: str | None = "0004_account_owned_data"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ensure_email_index() -> None:
    indexes = sa.inspect(op.get_bind()).get_indexes("users")
    if any(index.get("name") == "uq_users_email_lower" for index in indexes):
        return
    op.create_index(
        "uq_users_email_lower",
        "users",
        [sa.text("lower(email)")],
        unique=True,
    )


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("ck_users_role", type_="check")
        batch_op.create_check_constraint(
            "ck_users_role",
            "role IN ('viewer', 'importer', 'admin')",
        )
    _ensure_email_index()

    op.create_table(
        "user_invitations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('viewer', 'importer')",
            name="ck_user_invitations_role",
        ),
        sa.CheckConstraint(
            "length(email) > 0",
            name="ck_user_invitations_email",
        ),
        sa.UniqueConstraint("email", name="uq_user_invitations_email"),
        sa.UniqueConstraint("token_hash", name="uq_user_invitations_token_hash"),
    )


def downgrade() -> None:
    op.drop_table("user_invitations")
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("ck_users_role", type_="check")
        batch_op.create_check_constraint(
            "ck_users_role",
            "role IN ('viewer', 'importer')",
        )
    _ensure_email_index()
