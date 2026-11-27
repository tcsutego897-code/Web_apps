from __future__ import annotations

import math
import posixpath
import zipfile
import zlib
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from io import BytesIO
from typing import Any
from xml.etree import ElementTree
from xml.parsers.expat import ExpatError

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from app.config import get_settings
from app.services.import_common import (
    FieldPreview,
    ImportInputError,
    ImportResult,
    PreviewRow,
    build_import_fields,
    import_tabular_data,
    record_failed_preview,
)

PREVIEW_ROW_LIMIT = 5
MAX_ZIP_ENTRIES = 10_000
MAX_MERGED_RANGE_WARNINGS = 5
MAX_CELL_WARNINGS = 10
MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


@dataclass(frozen=True)
class WorkbookPreview:
    filename: str
    sheet_name: str
    visible_sheets: list[str]
    header_row: int
    fields: list[FieldPreview]
    sample_rows: list[PreviewRow]
    record_count: int
    warnings: list[str]


def _validate_archive(content: bytes) -> None:
    settings = get_settings()
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            members = archive.infolist()
            if len(members) > MAX_ZIP_ENTRIES:
                raise ImportInputError("Excelファイル内のファイル数が上限を超えています。")
            if "[Content_Types].xml" not in archive.namelist() or (
                "xl/workbook.xml" not in archive.namelist()
            ):
                raise ImportInputError("有効な .xlsx ファイルではありません。")
            if any(member.flag_bits & 0x1 for member in members):
                raise ImportInputError("暗号化されたExcelファイルは取り込めません。")
            if any(member.filename.casefold().endswith("vbaproject.bin") for member in members):
                raise ImportInputError("マクロを含むファイルは取り込めません。")
            total_uncompressed_size = sum(member.file_size for member in members)
            if total_uncompressed_size > settings.max_workbook_uncompressed_bytes:
                raise ImportInputError(
                    "展開後のファイルサイズが上限を超えています。"
                )
    except zipfile.BadZipFile as error:
        raise ImportInputError("Excelファイルが壊れているか、.xlsx形式ではありません。") from error
    except zipfile.LargeZipFile as error:
        raise ImportInputError("Excelファイル内のZIP構造を読み取れません。") from error


def _open_workbook_pair(content: bytes) -> tuple[Any, Any]:
    _validate_archive(content)
    formula_workbook = None
    try:
        formula_workbook = load_workbook(
            BytesIO(content),
            read_only=True,
            data_only=False,
            keep_links=False,
        )
        value_workbook = load_workbook(
            BytesIO(content),
            read_only=True,
            data_only=True,
            keep_links=False,
        )
    except (
        InvalidFileException,
        KeyError,
        OSError,
        ValueError,
        IndexError,
        ExpatError,
        zipfile.BadZipFile,
        zlib.error,
    ) as error:
        if formula_workbook is not None:
            formula_workbook.close()
        raise ImportInputError(
            "Excelの内容を読み取れません。ファイルが壊れていないか確認してください。"
        ) from error
    return formula_workbook, value_workbook


def _read_import_row(
    formula_cells: Sequence[Any],
    value_cells: Sequence[Any],
    fields: Sequence[FieldPreview],
    row_number: int,
    cell_warnings: list[str],
) -> dict[str, Any]:
    try:
        return _read_record(
            formula_cells,
            value_cells,
            fields,
            row_number,
            cell_warnings,
        )
    except ImportInputError:
        raise
    except (TypeError, ValueError, OverflowError) as error:
        raise ImportInputError(
            f"Excelの{row_number}行目の値を読み取れません。",
            source_row_number=row_number,
        ) from error


def _build_fields(header_cells: Sequence[Any]) -> tuple[list[FieldPreview], list[str]]:
    return build_import_fields([cell.value for cell in header_cells])


def _merged_ranges(content: bytes, sheet_name: str) -> list[str]:
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            workbook_root = ElementTree.fromstring(archive.read("xl/workbook.xml"))
            relationships_root = ElementTree.fromstring(
                archive.read("xl/_rels/workbook.xml.rels")
            )
            relationships = {
                relation.attrib["Id"]: relation.attrib["Target"]
                for relation in relationships_root.findall(
                    f"{{{PACKAGE_REL_NS}}}Relationship"
                )
            }
            worksheet_path = ""
            for sheet in workbook_root.findall(f".//{{{MAIN_NS}}}sheet"):
                if sheet.attrib.get("name") == sheet_name:
                    target = relationships.get(sheet.attrib.get(f"{{{REL_NS}}}id", ""))
                    if target:
                        if target.startswith("/"):
                            worksheet_path = posixpath.normpath(target.lstrip("/"))
                        elif target.startswith("xl/"):
                            worksheet_path = posixpath.normpath(target)
                        else:
                            worksheet_path = posixpath.normpath(
                                posixpath.join("xl", target)
                            )
                    break
            if not worksheet_path or worksheet_path not in archive.namelist():
                return []

            ranges: list[str] = []
            with archive.open(worksheet_path) as worksheet_xml:
                for _, element in ElementTree.iterparse(
                    worksheet_xml,
                    events=("end",),
                ):
                    if element.tag == f"{{{MAIN_NS}}}mergeCell":
                        if len(ranges) < MAX_MERGED_RANGE_WARNINGS:
                            ranges.append(element.attrib.get("ref", ""))
                    element.clear()
            return ranges
    except (zipfile.BadZipFile, KeyError, OSError, ElementTree.ParseError) as error:
        raise ImportInputError("結合セル情報を読み取れません。") from error


def _normalize_cell_value(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _read_record(
    formula_cells: Sequence[Any],
    value_cells: Sequence[Any],
    fields: Sequence[FieldPreview],
    row_number: int,
    cell_warnings: list[str],
) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for field, formula_cell, value_cell in zip(fields, formula_cells, value_cells):
        if value_cell.data_type == "e":
            raise ImportInputError(
                f"Excelの{row_number}行目（{formula_cell.coordinate}）にエラー値があります。",
                source_row_number=row_number,
            )
        if formula_cell.data_type == "f" and value_cell.value is None:
            if len(cell_warnings) < MAX_CELL_WARNINGS:
                cell_warnings.append(
                    f"{formula_cell.coordinate}: 保存済みの数式結果がないため空欄にしました。"
                )
            value = None
        else:
            value = _normalize_cell_value(value_cell.value)
        data[field.key] = value

    if all(value is None for value in data.values()):
        return {}
    return data


def _read_header_row(sheet: Any, header_row: int, column_count: int) -> list[Any]:
    try:
        return next(
            sheet.iter_rows(
                min_row=header_row,
                max_row=header_row,
                max_col=column_count,
            )
        )
    except StopIteration:
        raise ImportInputError("指定した行に見出しがありません。") from None
    except (
        InvalidFileException,
        KeyError,
        OSError,
        ValueError,
        IndexError,
        ExpatError,
        zipfile.BadZipFile,
        zlib.error,
    ) as error:
        raise ImportInputError("Excelの見出し行を読み取れません。") from error


def _ensure_worksheet_dimensions(sheet: Any) -> None:
    if sheet.max_column and sheet.max_row:
        return
    try:
        sheet.calculate_dimension(force=True)
    except UnboundLocalError:
        return


def _inspect_sheet(
    filename: str,
    content: bytes,
    sheet_name: str | None,
    header_row: int,
) -> WorkbookPreview:
    settings = get_settings()
    if header_row < 1:
        raise ImportInputError("見出し行は1以上の行番号を指定してください。")

    formula_workbook, value_workbook = _open_workbook_pair(content)
    try:
        visible_sheets = [
            worksheet.title
            for worksheet in formula_workbook.worksheets
            if worksheet.sheet_state == "visible"
        ]
        if not visible_sheets:
            raise ImportInputError("表示状態のシートがありません。")
        selected_sheet = sheet_name or visible_sheets[0]
        if selected_sheet not in visible_sheets:
            raise ImportInputError("選択したシートが見つからないか、非表示です。")

        formula_sheet = formula_workbook[selected_sheet]
        value_sheet = value_workbook[selected_sheet]
        _ensure_worksheet_dimensions(formula_sheet)
        _ensure_worksheet_dimensions(value_sheet)
        column_count = formula_sheet.max_column or 0
        if column_count < 1:
            raise ImportInputError("シートに見出しがありません。")
        if header_row > (formula_sheet.max_row or 0):
            raise ImportInputError("見出し行がシートの範囲外です。")

        fields, field_warnings = _build_fields(
            _read_header_row(formula_sheet, header_row, column_count)
        )
        warnings = list(field_warnings)
        cell_warnings: list[str] = []
        sample_rows: list[PreviewRow] = []
        record_count = 0

        formula_rows = formula_sheet.iter_rows(
            min_row=header_row + 1,
            max_col=len(fields),
        )
        value_rows = value_sheet.iter_rows(
            min_row=header_row + 1,
            max_col=len(fields),
        )
        try:
            for row_number, (formula_cells, value_cells) in enumerate(
                zip(formula_rows, value_rows),
                start=header_row + 1,
            ):
                data = _read_import_row(
                    formula_cells,
                    value_cells,
                    fields,
                    row_number,
                    cell_warnings,
                )
                if not data:
                    continue
                record_count += 1
                if record_count > settings.max_import_rows:
                    raise ImportInputError(
                        f"Excelの{row_number}行目で、取り込み可能な"
                        f"データ行数の上限（{settings.max_import_rows:,}行）を超えました。",
                        source_row_number=row_number,
                    )
                if len(sample_rows) < PREVIEW_ROW_LIMIT:
                    sample_rows.append(
                        PreviewRow(
                            row_number=row_number,
                            values=[data[field.key] for field in fields],
                        )
                    )
        except ImportInputError:
            raise
        except (
            InvalidFileException,
            KeyError,
            OSError,
            ValueError,
            IndexError,
            ExpatError,
            zipfile.BadZipFile,
            zlib.error,
        ) as error:
            raise ImportInputError(
                "Excelのデータ行を読み取れません。ファイルが壊れていないか確認してください。"
            ) from error

        if record_count == 0:
            raise ImportInputError(
                "見出しより下にデータがありません。見出し行を確認してください。"
            )

        if cell_warnings:
            warnings.append("数式の計算結果が保存されていないセルがあります。")
            warnings.extend(cell_warnings)
            if len(cell_warnings) == MAX_CELL_WARNINGS:
                warnings.append("表示できる数式の警告は先頭10件までです。")

        ranges = _merged_ranges(content, selected_sheet)
        if ranges:
            displayed_ranges = "、".join(ranges)
            warnings.append(
                f"結合セルがあります（{displayed_ranges}）。"
                "左上セルの値だけを使い、他のセルへ補完しません。"
            )

        return WorkbookPreview(
            filename=filename,
            sheet_name=selected_sheet,
            visible_sheets=visible_sheets,
            header_row=header_row,
            fields=fields,
            sample_rows=sample_rows,
            record_count=record_count,
            warnings=warnings,
        )
    finally:
        formula_workbook.close()
        value_workbook.close()


def preview_workbook(
    filename: str,
    content: bytes,
    sheet_name: str | None,
    header_row: int,
) -> WorkbookPreview:
    return _inspect_sheet(filename, content, sheet_name, header_row)


def list_visible_sheets(content: bytes) -> list[str]:
    formula_workbook, value_workbook = _open_workbook_pair(content)
    try:
        return [
            worksheet.title
            for worksheet in formula_workbook.worksheets
            if worksheet.sheet_state == "visible"
        ]
    finally:
        formula_workbook.close()
        value_workbook.close()


def _iter_import_records(
    content: bytes,
    preview: WorkbookPreview,
) -> Iterator[tuple[int, dict[str, Any]]]:
    formula_workbook, value_workbook = _open_workbook_pair(content)
    try:
        formula_sheet = formula_workbook[preview.sheet_name]
        value_sheet = value_workbook[preview.sheet_name]
        formula_rows = formula_sheet.iter_rows(
            min_row=preview.header_row + 1,
            max_col=len(preview.fields),
        )
        value_rows = value_sheet.iter_rows(
            min_row=preview.header_row + 1,
            max_col=len(preview.fields),
        )
        cell_warnings: list[str] = []
        count = 0
        try:
            for row_number, (formula_cells, value_cells) in enumerate(
                zip(formula_rows, value_rows),
                start=preview.header_row + 1,
            ):
                data = _read_import_row(
                    formula_cells,
                    value_cells,
                    preview.fields,
                    row_number,
                    cell_warnings,
                )
                if data:
                    count += 1
                    if count > get_settings().max_import_rows:
                        raise ImportInputError(
                            f"Excelの{row_number}行目で、プレビュー後に"
                            "データ行数の上限を超えました。",
                            source_row_number=row_number,
                        )
                    yield row_number, data
        except ImportInputError:
            raise
        except (
            InvalidFileException,
            KeyError,
            OSError,
            ValueError,
            IndexError,
            ExpatError,
            zipfile.BadZipFile,
            zlib.error,
        ) as error:
            raise ImportInputError(
                "Excelのデータ行を読み取れません。ファイルが壊れていないか確認してください。"
            ) from error
    finally:
        formula_workbook.close()
        value_workbook.close()


def import_workbook(
    filename: str,
    content: bytes,
    sheet_name: str,
    header_row: int,
    dataset_name: str,
    display_names: Sequence[str] | None,
    *,
    owner_id: int,
) -> ImportResult:
    def preview_factory() -> WorkbookPreview:
        return _inspect_sheet(filename, content, sheet_name, header_row)

    def records_factory(preview: WorkbookPreview) -> Iterator[tuple[int, dict[str, Any]]]:
        return _iter_import_records(content, preview)

    return import_tabular_data(
        filename=filename,
        sheet_name=sheet_name,
        dataset_name=dataset_name,
        display_names=display_names,
        owner_id=owner_id,
        source_format="Excel",
        preview_factory=preview_factory,
        records_factory=records_factory,
    )
