import logging
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Protocol, TypeVar

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_engine
from app.models import Dataset, DatasetField, ImportHistory, ImportIssue, Record

logger = logging.getLogger(__name__)
RECORD_BATCH_SIZE = 500


class ImportInputError(ValueError):
    """A user-facing error that should prevent an import."""

    def __init__(self, message: str, source_row_number: int | None = None) -> None:
        super().__init__(message)
        self.source_row_number = source_row_number


@dataclass(frozen=True)
class FieldPreview:
    key: str
    source_name: str
    display_name: str
    position: int


@dataclass(frozen=True)
class PreviewRow:
    row_number: int
    values: list[Any]


@dataclass(frozen=True)
class ImportResult:
    dataset_id: int
    dataset_name: str
    record_count: int
    warnings: list[str]
    source_format: str = "Excel"


class TabularPreview(Protocol):
    filename: str
    sheet_name: str
    header_row: int
    fields: list[FieldPreview]
    sample_rows: list[PreviewRow]
    record_count: int
    warnings: list[str]


PreviewT = TypeVar("PreviewT", bound=TabularPreview)


def build_import_fields(
    header_values: Sequence[Any],
) -> tuple[list[FieldPreview], list[str]]:
    fields: list[FieldPreview] = []
    warnings: list[str] = []
    seen_names: set[str] = set()

    for index, raw_value in enumerate(header_values):
        source_name = "" if raw_value is None else str(raw_value)
        normalized_name = source_name.strip().casefold()
        position = index
        display_name = source_name.strip() or f"列 {index + 1}"
        fields.append(
            FieldPreview(
                key=f"field_{index + 1}",
                source_name=source_name,
                display_name=display_name,
                position=position,
            )
        )
        if not normalized_name:
            warnings.append(
                f"{index + 1}列目の見出しが空です。表示名を設定してください。"
            )
        elif normalized_name in seen_names:
            warnings.append(
                f"見出し「{source_name}」が重複しています。列は別々に取り込みます。"
            )
        else:
            seen_names.add(normalized_name)

    if not fields or all(not field.source_name.strip() for field in fields):
        raise ImportInputError(
            "見出し行に項目名がありません。見出し行を選び直してください。"
        )
    return fields, warnings


def clean_display_names(
    fields: Sequence[FieldPreview],
    display_names: Sequence[str] | None,
) -> list[str]:
    if display_names is None:
        return [field.display_name for field in fields]
    if len(display_names) != len(fields):
        raise ImportInputError("項目名の数が列数と一致しません。")

    cleaned_names = []
    for field, display_name in zip(fields, display_names):
        name = display_name.strip() or field.display_name
        if len(name) > 255:
            raise ImportInputError("項目名は255文字以内にしてください。")
        cleaned_names.append(name)
    return cleaned_names


def finish_import_history(
    session: Session,
    history_id: int,
    status: str,
    success_count: int,
    failure_count: int,
    error_summary: str | None,
    issue: ImportInputError | None = None,
) -> None:
    history = session.scalar(
        select(ImportHistory).where(ImportHistory.id == history_id)
    )
    if history is None:
        raise RuntimeError("Import history disappeared during processing.")
    history.status = status
    history.success_count = success_count
    history.failure_count = failure_count
    history.error_summary = error_summary
    history.completed_at = datetime.now().astimezone()
    if issue is not None:
        history.issues.append(
            ImportIssue(
                source_row_number=issue.source_row_number,
                message=str(issue),
            )
        )
    session.commit()


def record_failed_preview(
    filename: str,
    sheet_name: str,
    error: ImportInputError,
    *,
    owner_id: int,
) -> None:
    with Session(get_engine()) as session:
        history = ImportHistory(
            owner_id=owner_id,
            source_filename=filename,
            sheet_name=sheet_name,
            status="failed",
            success_count=0,
            failure_count=1,
            error_summary=str(error),
            completed_at=datetime.now().astimezone(),
        )
        if error.source_row_number is not None:
            history.issues.append(
                ImportIssue(
                    source_row_number=error.source_row_number,
                    message=str(error),
                )
            )
        session.add(history)
        session.commit()


def import_tabular_data(
    *,
    filename: str,
    sheet_name: str,
    dataset_name: str,
    display_names: Sequence[str] | None,
    owner_id: int,
    source_format: str,
    preview_factory: Callable[[], PreviewT],
    records_factory: Callable[
        [PreviewT],
        Iterator[tuple[int, dict[str, Any]]],
    ],
) -> ImportResult:
    cleaned_dataset_name = dataset_name.strip()
    engine = get_engine()
    try:
        with Session(engine) as session:
            history = ImportHistory(
                owner_id=owner_id,
                source_filename=filename,
                sheet_name=sheet_name,
                status="pending",
            )
            session.add(history)
            session.flush()
            history_id = history.id
            session.commit()

            try:
                if not cleaned_dataset_name:
                    raise ImportInputError("データセット名を入力してください。")
                if len(cleaned_dataset_name) > 255:
                    raise ImportInputError("データセット名は255文字以内にしてください。")

                preview = preview_factory()
                names = clean_display_names(preview.fields, display_names)
                dataset = Dataset(
                    owner_id=owner_id,
                    name=cleaned_dataset_name,
                    source_filename=filename,
                    sheet_name=sheet_name,
                    record_count=preview.record_count,
                )
                with session.begin():
                    session.add(dataset)
                    session.flush()
                    session.add_all(
                        [
                            DatasetField(
                                dataset_id=dataset.id,
                                key=field.key,
                                source_name=field.source_name,
                                display_name=display_name,
                                position=field.position,
                                is_searchable=True,
                            )
                            for field, display_name in zip(preview.fields, names)
                        ]
                    )

                    record_batch: list[Record] = []
                    imported_count = 0
                    for row_number, data in records_factory(preview):
                        record_batch.append(
                            Record(
                                dataset_id=dataset.id,
                                source_row_number=row_number,
                                data=data,
                            )
                        )
                        imported_count += 1
                        if len(record_batch) >= RECORD_BATCH_SIZE:
                            session.add_all(record_batch)
                            session.flush()
                            record_batch.clear()
                    if record_batch:
                        session.add_all(record_batch)
                    if imported_count != preview.record_count:
                        raise ImportInputError(
                            "プレビュー後にデータ行数が変わりました。再度プレビューしてください。"
                        )

                    stored_history = session.get(ImportHistory, history_id)
                    if stored_history is None:
                        raise RuntimeError(
                            "Import history disappeared during processing."
                        )
                    stored_history.dataset = dataset
                    stored_history.status = "succeeded"
                    stored_history.success_count = imported_count
                    stored_history.failure_count = 0
                    stored_history.error_summary = None
                    stored_history.completed_at = datetime.now().astimezone()

                return ImportResult(
                    dataset_id=dataset.id,
                    dataset_name=cleaned_dataset_name,
                    record_count=preview.record_count,
                    warnings=preview.warnings,
                    source_format=source_format,
                )
            except ImportInputError as error:
                session.rollback()
                finish_import_history(
                    session,
                    history_id,
                    status="failed",
                    success_count=0,
                    failure_count=1,
                    error_summary=str(error),
                    issue=error if error.source_row_number is not None else None,
                )
                raise
            except SQLAlchemyError:
                session.rollback()
                finish_import_history(
                    session,
                    history_id,
                    status="failed",
                    success_count=0,
                    failure_count=1,
                    error_summary="保存処理に失敗したため、取り込み全体を取り消しました。",
                )
                raise
    except SQLAlchemyError:
        logger.exception("%s import failed: %s", source_format, filename)
        raise
