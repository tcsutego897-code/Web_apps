from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    literal_column,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.database import Base

JSON_VALUE = JSON().with_variant(JSONB(), "postgresql")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('viewer', 'importer')",
            name="ck_users_role",
        ),
        CheckConstraint("length(email) > 0", name="ck_users_email_not_empty"),
        CheckConstraint(
            "length(password_hash) > 0",
            name="ck_users_password_hash_not_empty",
        ),
        Index(
            "uq_users_email_lower",
            func.lower(literal_column("email")),
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="viewer")
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    @validates("email")
    def normalize_email(self, _key: str, email: str) -> str:
        del _key
        return email.strip().casefold()


class Dataset(Base):
    __tablename__ = "datasets"
    __table_args__ = (
        CheckConstraint("length(name) > 0", name="ck_datasets_name_not_empty"),
        CheckConstraint(
            "length(source_filename) > 0",
            name="ck_datasets_source_filename_not_empty",
        ),
        CheckConstraint(
            "length(sheet_name) > 0",
            name="ck_datasets_sheet_name_not_empty",
        ),
        CheckConstraint("record_count >= 0", name="ck_datasets_record_count"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    sheet_name: Mapped[str] = mapped_column(String(255), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    record_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    fields: Mapped[list[DatasetField]] = relationship(
        back_populates="dataset",
        cascade="all, delete-orphan",
        order_by="DatasetField.position",
    )
    records: Mapped[list[Record]] = relationship(
        back_populates="dataset",
        cascade="all, delete-orphan",
    )
    import_history: Mapped[list[ImportHistory]] = relationship(
        back_populates="dataset",
        passive_deletes=True,
    )


class DatasetField(Base):
    __tablename__ = "dataset_fields"
    __table_args__ = (
        UniqueConstraint("dataset_id", "key", name="uq_dataset_fields_key"),
        UniqueConstraint(
            "dataset_id",
            "position",
            name="uq_dataset_fields_position",
        ),
        CheckConstraint("length(key) > 0", name="ck_dataset_fields_key_not_empty"),
        CheckConstraint(
            "position >= 0",
            name="ck_dataset_fields_position",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"),
        nullable=False,
    )
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    source_name: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
        server_default="",
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    is_searchable: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )

    dataset: Mapped[Dataset] = relationship(back_populates="fields")


class Record(Base):
    __tablename__ = "records"
    __table_args__ = (
        UniqueConstraint(
            "dataset_id",
            "source_row_number",
            name="uq_records_dataset_source_row",
        ),
        CheckConstraint(
            "source_row_number > 0",
            name="ck_records_source_row_number",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON_VALUE, nullable=False)

    dataset: Mapped[Dataset] = relationship(back_populates="records")


class ImportHistory(Base):
    __tablename__ = "import_histories"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'succeeded', 'partial', 'failed')",
            name="ck_import_histories_status",
        ),
        CheckConstraint(
            "success_count >= 0",
            name="ck_import_histories_success_count",
        ),
        CheckConstraint(
            "failure_count >= 0",
            name="ck_import_histories_failure_count",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int | None] = mapped_column(
        ForeignKey("datasets.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    sheet_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        server_default="pending",
    )
    success_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    failure_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    error_summary: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    dataset: Mapped[Dataset | None] = relationship(back_populates="import_history")
    issues: Mapped[list[ImportIssue]] = relationship(
        back_populates="history",
        cascade="all, delete-orphan",
    )


class ImportIssue(Base):
    __tablename__ = "import_issues"
    __table_args__ = (
        CheckConstraint(
            "source_row_number IS NULL OR source_row_number > 0",
            name="ck_import_issues_source_row_number",
        ),
        CheckConstraint(
            "length(message) > 0",
            name="ck_import_issues_message_not_empty",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    import_history_id: Mapped[int] = mapped_column(
        ForeignKey("import_histories.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_row_number: Mapped[int | None] = mapped_column(Integer)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    history: Mapped[ImportHistory] = relationship(back_populates="issues")
