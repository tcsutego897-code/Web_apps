"""Add dataset, record, import history, and user tables.

Revision ID: 0002_dataset_and_user_schema
Revises: 0001_phase_one_baseline
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0002_dataset_and_user_schema"
down_revision: str | None = "0001_phase_one_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

json_value = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('viewer', 'importer')",
            name="ck_users_role",
        ),
        sa.CheckConstraint("length(email) > 0", name="ck_users_email_not_empty"),
        sa.CheckConstraint(
            "length(password_hash) > 0",
            name="ck_users_password_hash_not_empty",
        ),
    )
    op.create_index(
        "uq_users_email_lower",
        "users",
        [sa.text("lower(email)")],
        unique=True,
    )

    op.create_table(
        "datasets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("source_filename", sa.String(length=255), nullable=False),
        sa.Column("sheet_name", sa.String(length=255), nullable=False),
        sa.Column(
            "imported_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("record_count", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint("length(name) > 0", name="ck_datasets_name_not_empty"),
        sa.CheckConstraint(
            "length(source_filename) > 0",
            name="ck_datasets_source_filename_not_empty",
        ),
        sa.CheckConstraint(
            "length(sheet_name) > 0",
            name="ck_datasets_sheet_name_not_empty",
        ),
        sa.CheckConstraint("record_count >= 0", name="ck_datasets_record_count"),
    )

    op.create_table(
        "import_histories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "dataset_id",
            sa.Integer(),
            sa.ForeignKey("datasets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_filename", sa.String(length=255), nullable=False),
        sa.Column("sheet_name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("success_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failure_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'succeeded', 'partial', 'failed')",
            name="ck_import_histories_status",
        ),
        sa.CheckConstraint(
            "success_count >= 0",
            name="ck_import_histories_success_count",
        ),
        sa.CheckConstraint(
            "failure_count >= 0",
            name="ck_import_histories_failure_count",
        ),
    )

    op.create_table(
        "dataset_fields",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "dataset_id",
            sa.Integer(),
            sa.ForeignKey("datasets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("source_name", sa.Text(), server_default="", nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("is_searchable", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.UniqueConstraint("dataset_id", "key", name="uq_dataset_fields_key"),
        sa.UniqueConstraint("dataset_id", "position", name="uq_dataset_fields_position"),
        sa.CheckConstraint("length(key) > 0", name="ck_dataset_fields_key_not_empty"),
        sa.CheckConstraint("position >= 0", name="ck_dataset_fields_position"),
    )

    op.create_table(
        "records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "dataset_id",
            sa.Integer(),
            sa.ForeignKey("datasets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_row_number", sa.Integer(), nullable=False),
        sa.Column("data", json_value, nullable=False),
        sa.UniqueConstraint(
            "dataset_id",
            "source_row_number",
            name="uq_records_dataset_source_row",
        ),
        sa.CheckConstraint(
            "source_row_number > 0",
            name="ck_records_source_row_number",
        ),
    )


def downgrade() -> None:
    op.drop_table("records")
    op.drop_table("dataset_fields")
    op.drop_table("import_histories")
    op.drop_table("datasets")
    op.drop_index("uq_users_email_lower", table_name="users")
    op.drop_table("users")
