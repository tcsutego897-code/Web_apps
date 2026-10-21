import logging

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.services.excel_import import (
    ImportInputError,
    WorkbookPreview,
    import_workbook,
    list_visible_sheets,
    preview_workbook,
    record_failed_preview,
)
from app.services.upload_storage import (
    UploadError,
    delete_staged_upload,
    normalize_filename,
    read_staged_upload,
    read_upload,
    stage_upload,
)
from app.web import templates

router = APIRouter()
logger = logging.getLogger(__name__)


def render_import(
    request: Request,
    template_name: str,
    *,
    status_code: int = 200,
    **context: object,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name=template_name,
        context={"app_name": get_settings().app_name, **context},
        status_code=status_code,
    )


def parse_header_row(value: str) -> int:
    try:
        return int(value)
    except ValueError as error:
        raise ImportInputError("見出し行は1以上の行番号で入力してください。") from error


def render_preview(
    request: Request,
    *,
    filename: str = "",
    staging_token: str = "",
    sheet_name: str = "",
    header_row: str = "1",
    visible_sheets: list[str] | None = None,
    preview: WorkbookPreview | None = None,
    error_message: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return render_import(
        request,
        "pages/import_preview.html",
        status_code=status_code,
        filename=filename,
        staging_token=staging_token,
        sheet_name=sheet_name,
        header_row=header_row,
        visible_sheets=visible_sheets or [],
        preview=preview,
        error_message=error_message,
        default_dataset_name=(
            f"{filename.rsplit('.', 1)[0]} - {sheet_name}" if filename else ""
        ),
    )


@router.post("/import/preview", response_class=HTMLResponse, name="import_preview")
async def import_preview_page(
    request: Request,
    upload: UploadFile | None = File(None),
    staging_token: str = Form(""),
    filename: str = Form(""),
    sheet_name: str = Form(""),
    header_row: str = Form("1"),
) -> HTMLResponse:
    created_stage = False
    content = b""
    visible_sheets: list[str] = []

    try:
        if upload is not None and upload.filename:
            filename, content = await read_upload(upload)
            staging_token = stage_upload(content)
            created_stage = True
        elif staging_token:
            filename = normalize_filename(filename)
            content = read_staged_upload(staging_token)
        else:
            raise UploadError("Excelファイルを選択してください。")

        row_number = parse_header_row(header_row)
        preview = preview_workbook(
            filename,
            content,
            sheet_name or None,
            row_number,
        )
        visible_sheets = preview.visible_sheets
    except (ImportInputError, UploadError) as error:
        if content:
            try:
                visible_sheets = list_visible_sheets(content)
            except ImportInputError:
                if created_stage:
                    delete_staged_upload(staging_token)
                    staging_token = ""
        else:
            staging_token = ""
        status_code = 400
        error_message = str(error)
        if (
            isinstance(error, ImportInputError)
            and error.source_row_number is not None
            and filename
        ):
            selected_sheet = sheet_name or (
                visible_sheets[0] if visible_sheets else ""
            )
            try:
                record_failed_preview(filename, selected_sheet, error)
            except SQLAlchemyError:
                logger.exception("Could not record an Excel row validation error")
                error_message += " エラー履歴も保存できませんでした。"
                status_code = 503
        return render_preview(
            request,
            filename=filename,
            staging_token=staging_token,
            sheet_name=sheet_name,
            header_row=header_row,
            visible_sheets=visible_sheets,
            error_message=error_message,
            status_code=status_code,
        )

    return render_preview(
        request,
        filename=filename,
        staging_token=staging_token,
        sheet_name=preview.sheet_name,
        header_row=str(preview.header_row),
        visible_sheets=preview.visible_sheets,
        preview=preview,
    )


@router.post("/import/confirm", response_class=HTMLResponse, name="import_confirm")
def confirm_import_page(
    request: Request,
    staging_token: str = Form(""),
    filename: str = Form(""),
    sheet_name: str = Form(""),
    header_row: str = Form("1"),
    dataset_name: str = Form(""),
    display_names: list[str] | None = Form(None),
) -> HTMLResponse:
    content = b""
    try:
        filename = normalize_filename(filename)
        content = read_staged_upload(staging_token)
        result = import_workbook(
            filename=filename,
            content=content,
            sheet_name=sheet_name,
            header_row=parse_header_row(header_row),
            dataset_name=dataset_name,
            display_names=display_names,
        )
    except (ImportInputError, UploadError) as error:
        try:
            visible_sheets = list_visible_sheets(content)
        except ImportInputError:
            visible_sheets = []
        if not content:
            staging_token = ""
        return render_preview(
            request,
            filename=filename,
            staging_token=staging_token,
            sheet_name=sheet_name,
            header_row=header_row,
            visible_sheets=visible_sheets,
            error_message=str(error),
            status_code=400,
        )
    except SQLAlchemyError:
        logger.exception("Database error while confirming Excel import")
        try:
            visible_sheets = list_visible_sheets(content)
        except ImportInputError:
            visible_sheets = []
        return render_preview(
            request,
            filename=filename,
            staging_token=staging_token,
            sheet_name=sheet_name,
            header_row=header_row,
            visible_sheets=visible_sheets,
            error_message="データベースに接続できないか、保存に失敗しました。設定と接続状態を確認してください。",
            status_code=503,
        )

    delete_staged_upload(staging_token)
    return render_import(
        request,
        "pages/import_result.html",
        result=result,
    )
