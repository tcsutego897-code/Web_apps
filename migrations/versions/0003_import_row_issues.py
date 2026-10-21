"""Add row-level import errors.

Revision ID: 0003_import_row_issues
Revises: 0002_dataset_and_user_schema
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_import_row_issues"
down_revision: str | None = "0002_dataset_and_user_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "import_issues",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "import_history_id",
            sa.Integer(),
            sa.ForeignKey("import_histories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_row_number", sa.Integer(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "source_row_number IS NULL OR source_row_number > 0",
            name="ck_import_issues_source_row_number",
        ),
        sa.CheckConstraint(
            "length(message) > 0",
            name="ck_import_issues_message_not_empty",
        ),
    )


def downgrade() -> None:
    op.drop_table("import_issues")
