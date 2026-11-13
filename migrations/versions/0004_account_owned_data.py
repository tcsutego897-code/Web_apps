"""Add account ownership to datasets and import histories.

Revision ID: 0004_account_owned_data
Revises: 0003_import_row_issues
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_account_owned_data"
down_revision: str | None = "0003_import_row_issues"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("datasets") as batch_op:
        batch_op.add_column(sa.Column("owner_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_datasets_owner_id_users",
            "users",
            ["owner_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_datasets_owner_id", ["owner_id"])

    with op.batch_alter_table("import_histories") as batch_op:
        batch_op.add_column(sa.Column("owner_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_import_histories_owner_id_users",
            "users",
            ["owner_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("import_histories") as batch_op:
        batch_op.drop_constraint(
            "fk_import_histories_owner_id_users",
            type_="foreignkey",
        )
        batch_op.drop_column("owner_id")

    with op.batch_alter_table("datasets") as batch_op:
        batch_op.drop_index("ix_datasets_owner_id")
        batch_op.drop_constraint(
            "fk_datasets_owner_id_users",
            type_="foreignkey",
        )
        batch_op.drop_column("owner_id")
