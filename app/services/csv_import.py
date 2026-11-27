import csv
import io
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from app.config import get_settings
from app.services.import_common import (
    FieldPreview,
    ImportInputError,
    ImportResult,
    PreviewRow,
    build_import_fields,
    import_tabular_data,
)

CSV_ENCODINGS = {
    "utf-8-sig": "UTF-8（BOM対応）",
    "cp932": "Shift-JIS / Windows-31J",
}
CSV_DELIMITERS = {
    ",": "カンマ (,)",
    "\t": "タブ",
    ";": "セミコロン (;)",
}


@dataclass(frozen=True)
class CSVPreview:
    filename: str
    sheet_name: str
    header_row: int
    fields: list[FieldPreview]
    sample_rows: list[PreviewRow]
    record_count: int
    warnings: list[str]
    encoding: str
    delimiter: str


def _validate_options(encoding: str, delimiter: str, header_row: int) -> None:
    if encoding not in CSV_ENCODINGS:
        raise ImportInputError("文字コードを選び直してください。")
    if delimiter not in CSV_DELIMITERS:
        raise ImportInputError("区切り文字を選び直してください。")
    if header_row < 1:
        raise ImportInputError("見出し行は1以上の行番号を指定してください。")


def _iter_csv_rows(
    content: bytes,
    *,
    encoding: str,
    delimiter: str,
) -> Iterator[tuple[int, list[str]]]:
    try:
        text = content.decode(encoding, errors="strict")
    except UnicodeDecodeError as error:
        raise ImportInputError(
            f"CSVを{CSV_ENCODINGS[encoding]}として読み取れません。"
            "文字コードを確認してください。"
        ) from error

    reader = csv.reader(
        io.StringIO(text, newline=""),
        delimiter=delimiter,
        strict=True,
    )
    while True:
        row_number = reader.line_num + 1
        try:
            values = next(reader)
        except StopIteration:
            return
        except csv.Error as error:
            error_row = max(reader.line_num, row_number)
            raise ImportInputError(
                f"CSVの{error_row}行目を読み取れません。引用符や区切りを確認してください。",
                source_row_number=error_row,
            ) from error
        yield row_number, values


def _read_header(
    rows: Iterator[tuple[int, list[str]]],
    header_row: int,
) -> list[str]:
    for row_number, values in rows:
        if row_number < header_row:
            continue
        if row_number > header_row:
            break
        if not values or all(not value.strip() for value in values):
            raise ImportInputError(
                "指定した見出し行が空です。見出し行を選び直してください。"
            )
        return values
    raise ImportInputError("見出し行がCSVの範囲外です。")


def _iter_records(
    content: bytes,
    *,
    encoding: str,
    delimiter: str,
    header_row: int,
    fields: Sequence[FieldPreview],
    skipped_empty_rows: list[int] | None = None,
) -> Iterator[tuple[int, dict[str, Any]]]:
    rows = _iter_csv_rows(content, encoding=encoding, delimiter=delimiter)
    _read_header(rows, header_row)

    for row_number, values in rows:
        if not values or all(not value.strip() for value in values):
            if skipped_empty_rows is not None:
                skipped_empty_rows[0] += 1
            continue
        if len(values) != len(fields):
            raise ImportInputError(
                f"CSVの{row_number}行目は列数が見出しと一致しません"
                f"（見出し{len(fields)}列、データ{len(values)}列）。",
                source_row_number=row_number,
            )
        data = {
            field.key: (value if value != "" else None)
            for field, value in zip(fields, values)
        }
        if all(value is None for value in data.values()):
            if skipped_empty_rows is not None:
                skipped_empty_rows[0] += 1
            continue
        yield row_number, data


def preview_csv(
    filename: str,
    content: bytes,
    *,
    encoding: str = "utf-8-sig",
    delimiter: str = ",",
    header_row: int = 1,
) -> CSVPreview:
    _validate_options(encoding, delimiter, header_row)
    rows = _iter_csv_rows(content, encoding=encoding, delimiter=delimiter)
    header_values = _read_header(rows, header_row)
    fields, warnings = build_import_fields(header_values)
    sample_rows: list[PreviewRow] = []
    skipped_empty_rows = [0]
    record_count = 0

    for row_number, data in _iter_records(
        content,
        encoding=encoding,
        delimiter=delimiter,
        header_row=header_row,
        fields=fields,
        skipped_empty_rows=skipped_empty_rows,
    ):
        record_count += 1
        if record_count > get_settings().max_import_rows:
            raise ImportInputError(
                f"CSVの{row_number}行目で、取り込み可能なデータ行数の上限"
                f"（{get_settings().max_import_rows:,}行）を超えました。",
                source_row_number=row_number,
            )
        if len(sample_rows) < 5:
            sample_rows.append(
                PreviewRow(
                    row_number=row_number,
                    values=[data[field.key] for field in fields],
                )
            )

    if record_count == 0:
        raise ImportInputError("見出しより下にデータがありません。")
    if skipped_empty_rows[0]:
        warnings.append(
            f"空行を{skipped_empty_rows[0]}行スキップします。"
        )

    return CSVPreview(
        filename=filename,
        sheet_name="CSV",
        header_row=header_row,
        fields=fields,
        sample_rows=sample_rows,
        record_count=record_count,
        warnings=warnings,
        encoding=encoding,
        delimiter=delimiter,
    )


def import_csv(
    filename: str,
    content: bytes,
    *,
    encoding: str,
    delimiter: str,
    header_row: int,
    dataset_name: str,
    display_names: Sequence[str] | None,
    owner_id: int,
) -> ImportResult:
    def preview_factory() -> CSVPreview:
        return preview_csv(
            filename,
            content,
            encoding=encoding,
            delimiter=delimiter,
            header_row=header_row,
        )

    def records_factory(
        preview: CSVPreview,
    ) -> Iterator[tuple[int, dict[str, Any]]]:
        return _iter_records(
            content,
            encoding=preview.encoding,
            delimiter=preview.delimiter,
            header_row=preview.header_row,
            fields=preview.fields,
        )

    return import_tabular_data(
        filename=filename,
        sheet_name="CSV",
        dataset_name=dataset_name,
        display_names=display_names,
        owner_id=owner_id,
        source_format="CSV",
        preview_factory=preview_factory,
        records_factory=records_factory,
    )
