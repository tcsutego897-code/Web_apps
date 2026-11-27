import logging
from datetime import date, datetime, time
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import and_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_engine
from app.models import Dataset, DatasetField, ImportHistory, Record, User
from app.security import (
    get_csrf_token,
    require_authenticated_user,
    require_importer,
    verify_csrf_token,
)
from app.services.search import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    MAX_SEARCH_CONDITIONS,
    SearchCondition,
    SearchInputError,
    search_records,
)
from app.web import templates

router = APIRouter()
logger = logging.getLogger(__name__)
SEARCH_QUERY_KEYS = {
    "dataset_id",
    "field_key",
    "value",
    "page",
    "page_size",
    "sort_field",
    "sort_direction",
}
SEARCH_LIST_FIELD_LIMIT = 5
IMPORT_HISTORY_STATUS_LABELS = {
    "pending": "処理中",
    "succeeded": "成功",
    "partial": "一部失敗",
    "failed": "失敗",
}


def render_page(
    request: Request,
    template_name: str,
    *,
    status_code: int = 200,
    **context: object,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name=template_name,
        context={
            "app_name": get_settings().app_name,
            "csrf_token": get_csrf_token(request),
            "current_user": getattr(request.state, "current_user", None),
            **context,
        },
        status_code=status_code,
    )


def build_search_url(request: Request) -> str:
    parameters = [
        (key, value)
        for key, value in request.query_params.multi_items()
        if key in SEARCH_QUERY_KEYS
    ]
    if not parameters:
        return "/search"
    return "/search?" + urlencode(parameters)


def format_record_value(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, str):
        if "T" in value or " " in value:
            try:
                parsed_datetime = datetime.fromisoformat(value)
                return parsed_datetime.isoformat(sep=" ", timespec="auto")
            except ValueError:
                pass
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            try:
                parsed_time = time.fromisoformat(value)
                timespec = (
                    "auto" if parsed_time.second or parsed_time.microsecond else "minutes"
                )
                return parsed_time.isoformat(timespec=timespec)
            except ValueError:
                return value
    return str(value)


@router.get("/", response_class=HTMLResponse, name="datasets")
def datasets_page(
    request: Request,
    current_user: User = Depends(require_authenticated_user),
    deleted: bool = False,
) -> HTMLResponse:
    try:
        with Session(get_engine()) as session:
            datasets = session.scalars(
                select(Dataset)
                .where(Dataset.owner_id == current_user.id)
                .order_by(Dataset.imported_at.desc(), Dataset.id.desc())
            ).all()
    except SQLAlchemyError:
        logger.exception("Could not load the dataset list")
        return render_page(
            request,
            "pages/datasets.html",
            status_code=503,
            datasets=[],
            error_message="データベースに接続できません。設定と接続状態を確認してください。",
        )

    return render_page(
        request,
        "pages/datasets.html",
        datasets=datasets,
        success_message=("データセットを削除しました。" if deleted else None),
    )


def load_import_history_rows(
    session: Session,
    *,
    owner_id: int,
    dataset_id: int | None = None,
) -> list[dict[str, object]]:
    statement = (
        select(ImportHistory, Dataset.name)
        .outerjoin(
            Dataset,
            and_(
                Dataset.id == ImportHistory.dataset_id,
                Dataset.owner_id == owner_id,
            ),
        )
        .where(ImportHistory.owner_id == owner_id)
        .order_by(ImportHistory.started_at.desc(), ImportHistory.id.desc())
    )
    if dataset_id is not None:
        statement = statement.where(ImportHistory.dataset_id == dataset_id)

    history_rows = []
    for history, dataset_name in session.execute(statement):
        history_rows.append(
            {
                "dataset_id": history.dataset_id,
                "dataset_name": dataset_name or "未作成または削除済み",
                "source_filename": history.source_filename,
                "sheet_name": history.sheet_name,
                "status_label": IMPORT_HISTORY_STATUS_LABELS.get(
                    history.status,
                    history.status,
                ),
                "success_count": history.success_count,
                "failure_count": history.failure_count,
                "error_summary": history.error_summary,
                "started_at": history.started_at,
            }
        )
    return history_rows


def render_dataset_management_page(
    request: Request,
    *,
    dataset: Dataset | None,
    history_rows: list[dict[str, object]],
    status_code: int = 200,
    error_message: str | None = None,
    success_message: str | None = None,
    submitted_name: str | None = None,
) -> HTMLResponse:
    return render_page(
        request,
        "pages/dataset_manage.html",
        status_code=status_code,
        dataset=dataset,
        history_rows=history_rows,
        error_message=error_message,
        success_message=success_message,
        submitted_name=submitted_name,
    )


@router.get(
    "/datasets/{dataset_id}/manage",
    response_class=HTMLResponse,
    name="dataset_manage",
)
def dataset_management_page(
    request: Request,
    dataset_id: int,
    current_user: User = Depends(require_authenticated_user),
    updated: bool = False,
) -> HTMLResponse:
    try:
        with Session(get_engine()) as session:
            dataset = session.scalar(
                select(Dataset).where(
                    Dataset.id == dataset_id,
                    Dataset.owner_id == current_user.id,
                )
            )
            if dataset is None:
                raise HTTPException(status_code=404, detail="Dataset not found")
            history_rows = load_import_history_rows(
                session,
                owner_id=current_user.id,
                dataset_id=dataset.id,
            )
    except SQLAlchemyError:
        logger.exception(
            "Could not load management page for dataset %s",
            dataset_id,
        )
        return render_dataset_management_page(
            request,
            dataset=None,
            history_rows=[],
            status_code=503,
            error_message="データベースに接続できないか、情報を読み込めませんでした。",
        )

    return render_dataset_management_page(
        request,
        dataset=dataset,
        history_rows=history_rows,
        success_message=("データセット名を変更しました。" if updated else None),
    )


@router.post(
    "/datasets/{dataset_id}/rename",
    response_class=HTMLResponse,
    response_model=None,
    name="dataset_rename",
)
def rename_dataset_page(
    request: Request,
    dataset_id: int,
    dataset_name: Annotated[str, Form()],
    _csrf_valid: Annotated[None, Depends(verify_csrf_token)],
    current_user: User = Depends(require_authenticated_user),
) -> HTMLResponse | RedirectResponse:
    del _csrf_valid
    cleaned_name = dataset_name.strip()
    if not cleaned_name:
        error_message = "データセット名を入力してください。"
    elif len(cleaned_name) > 255:
        error_message = "データセット名は255文字以内にしてください。"
    else:
        error_message = None

    try:
        with Session(get_engine()) as session:
            dataset = session.scalar(
                select(Dataset).where(
                    Dataset.id == dataset_id,
                    Dataset.owner_id == current_user.id,
                )
            )
            if dataset is None:
                raise HTTPException(status_code=404, detail="Dataset not found")
            if error_message is not None:
                history_rows = load_import_history_rows(
                    session,
                    owner_id=current_user.id,
                    dataset_id=dataset.id,
                )
                return render_dataset_management_page(
                    request,
                    dataset=dataset,
                    history_rows=history_rows,
                    status_code=400,
                    error_message=error_message,
                    submitted_name=dataset_name,
                )

            dataset.name = cleaned_name
            session.commit()
    except SQLAlchemyError:
        logger.exception(
            "Could not rename dataset %s for user %s",
            dataset_id,
            current_user.id,
        )
        return render_dataset_management_page(
            request,
            dataset=None,
            history_rows=[],
            status_code=503,
            error_message="データベースに接続できないか、変更を保存できませんでした。",
        )

    return RedirectResponse(
        f"/datasets/{dataset_id}/manage?updated=1",
        status_code=303,
    )


@router.post(
    "/datasets/{dataset_id}/delete",
    response_model=None,
    name="dataset_delete",
)
def delete_dataset_page(
    request: Request,
    dataset_id: int,
    _csrf_valid: Annotated[None, Depends(verify_csrf_token)],
    current_user: User = Depends(require_authenticated_user),
    confirm_delete: Annotated[str | None, Form()] = None,
) -> HTMLResponse | RedirectResponse:
    del _csrf_valid
    try:
        with Session(get_engine()) as session:
            dataset = session.scalar(
                select(Dataset).where(
                    Dataset.id == dataset_id,
                    Dataset.owner_id == current_user.id,
                )
            )
            if dataset is None:
                raise HTTPException(status_code=404, detail="Dataset not found")
            if confirm_delete != "yes":
                history_rows = load_import_history_rows(
                    session,
                    owner_id=current_user.id,
                    dataset_id=dataset.id,
                )
                return render_dataset_management_page(
                    request,
                    dataset=dataset,
                    history_rows=history_rows,
                    status_code=400,
                    error_message="削除する場合は確認欄にチェックしてください。",
                )

            session.delete(dataset)
            session.commit()
    except SQLAlchemyError:
        logger.exception(
            "Could not delete dataset %s for user %s",
            dataset_id,
            current_user.id,
        )
        return render_dataset_management_page(
            request,
            dataset=None,
            history_rows=[],
            status_code=503,
            error_message="データベースに接続できないか、削除に失敗しました。",
        )

    return RedirectResponse("/?deleted=1", status_code=303)


@router.get(
    "/imports/history",
    response_class=HTMLResponse,
    name="import_history",
)
def import_history_page(
    request: Request,
    current_user: User = Depends(require_authenticated_user),
    dataset_id: int | None = None,
) -> HTMLResponse:
    try:
        with Session(get_engine()) as session:
            dataset_name = None
            if dataset_id is not None:
                dataset = session.scalar(
                    select(Dataset).where(
                        Dataset.id == dataset_id,
                        Dataset.owner_id == current_user.id,
                    )
                )
                if dataset is None:
                    raise HTTPException(status_code=404, detail="Dataset not found")
                dataset_name = dataset.name

            history_rows = load_import_history_rows(
                session,
                owner_id=current_user.id,
                dataset_id=dataset_id,
            )
    except SQLAlchemyError:
        logger.exception(
            "Could not load import history for user %s",
            current_user.id,
        )
        return render_page(
            request,
            "pages/import_history.html",
            status_code=503,
            history_rows=[],
            dataset_name=None,
            error_message="データベースに接続できないか、履歴を読み込めませんでした。",
        )

    return render_page(
        request,
        "pages/import_history.html",
        history_rows=history_rows,
        dataset_name=dataset_name,
        dataset_id=dataset_id,
        error_message=None,
    )


@router.get("/search", response_class=HTMLResponse, name="search")
def search_page(
    request: Request,
    current_user: User = Depends(require_authenticated_user),
    dataset_id: int | None = None,
    field_key: list[str] = Query(default=[]),
    value: list[str] = Query(default=[]),
    page: str = "1",
    page_size: int = DEFAULT_PAGE_SIZE,
    sort_field: str = "",
    sort_direction: str = "asc",
) -> HTMLResponse:
    error_message = None
    results = None
    condition_rows = []

    try:
        try:
            page_number = int(page)
        except ValueError as error:
            raise SearchInputError("ページ番号は整数で指定してください。") from error

        if len(field_key) != len(value):
            raise SearchInputError("検索条件の項目と検索語をそろえてください。")
        if len(field_key) > MAX_SEARCH_CONDITIONS:
            raise SearchInputError(
                f"検索条件は{MAX_SEARCH_CONDITIONS}個まで指定できます。"
            )

        conditions = []
        for selected_field, search_value in zip(field_key, value):
            if not selected_field and not search_value.strip():
                continue
            if not selected_field or not search_value.strip():
                raise SearchInputError("各検索条件の項目と検索語を入力してください。")
            conditions.append(
                SearchCondition(
                    field_key=selected_field,
                    value=search_value,
                )
            )

        with Session(get_engine()) as session:
            results = search_records(
                session,
                owner_id=current_user.id,
                dataset_id=dataset_id,
                requested_conditions=conditions,
                page=page_number,
                page_size=page_size,
                sort_field=sort_field,
                sort_direction=sort_direction,
                searched=bool(dataset_id),
            )

        if results.selected_dataset is not None:
            condition_rows = [
                {
                    "field_key": condition.field_key,
                    "value": condition.value,
                }
                for condition in results.conditions
            ]
    except SearchInputError as error:
        error_message = str(error)
    except SQLAlchemyError:
        logger.exception("Could not search dataset records")
        error_message = "データベースに接続できないか、検索に失敗しました。設定と接続状態を確認してください。"
        return render_page(
            request,
            "pages/search.html",
            status_code=503,
            error_message=error_message,
            results=None,
            condition_rows=condition_rows,
            max_search_conditions=MAX_SEARCH_CONDITIONS,
            max_page_size=MAX_PAGE_SIZE,
            previous_page_url=None,
            next_page_url=None,
        )

    if results is None and error_message:
        try:
            with Session(get_engine()) as session:
                datasets = session.scalars(
                    select(Dataset)
                    .where(Dataset.owner_id == current_user.id)
                    .order_by(Dataset.name, Dataset.id)
                ).all()
        except SQLAlchemyError:
            logger.exception("Could not load datasets for invalid search request")
            return render_page(
                request,
                "pages/search.html",
                status_code=503,
                error_message="データベースに接続できません。設定と接続状態を確認してください。",
                results=None,
                condition_rows=condition_rows,
                max_search_conditions=MAX_SEARCH_CONDITIONS,
                max_page_size=MAX_PAGE_SIZE,
                previous_page_url=None,
                next_page_url=None,
            )
    else:
        datasets = results.datasets if results is not None else []

    previous_page_url = None
    next_page_url = None
    if results is not None and results.total_pages:
        query_parameters = [("dataset_id", str(results.selected_dataset.id))]
        for condition in results.conditions:
            query_parameters.extend(
                [
                    ("field_key", condition.field_key),
                    ("value", condition.value),
                ]
            )
        query_parameters.extend(
            [
                ("page_size", str(results.page_size)),
                ("sort_field", results.sort_field),
                ("sort_direction", results.sort_direction),
            ]
        )
        if results.page > 1:
            previous_page_url = "/search?" + urlencode(
                [*query_parameters, ("page", str(results.page - 1))]
            )
        if results.page < results.total_pages:
            next_page_url = "/search?" + urlencode(
                [*query_parameters, ("page", str(results.page + 1))]
            )

    return render_page(
        request,
        "pages/search.html",
        status_code=400 if error_message else 200,
        results=results,
        datasets=datasets,
        display_fields=(
            results.fields[:SEARCH_LIST_FIELD_LIMIT] if results is not None else []
        ),
        detail_query=urlencode(
            [
                (key, value)
                for key, value in request.query_params.multi_items()
                if key in SEARCH_QUERY_KEYS
            ]
        ),
        condition_rows=condition_rows,
        error_message=error_message,
        max_search_conditions=MAX_SEARCH_CONDITIONS,
        max_page_size=MAX_PAGE_SIZE,
        previous_page_url=previous_page_url,
        next_page_url=next_page_url,
    )


@router.get(
    "/datasets/{dataset_id}/records/{record_id}",
    response_class=HTMLResponse,
    name="record_detail",
)
def record_detail_page(
    request: Request,
    dataset_id: int,
    record_id: int,
    current_user: User = Depends(require_authenticated_user),
) -> HTMLResponse:
    try:
        with Session(get_engine()) as session:
            dataset = session.scalar(
                select(Dataset).where(
                    Dataset.id == dataset_id,
                    Dataset.owner_id == current_user.id,
                )
            )
            record = session.scalar(
                select(Record).where(
                    Record.id == record_id,
                    Record.dataset_id == dataset_id,
                )
            )
            if dataset is None or record is None:
                raise HTTPException(status_code=404, detail="Record not found")
            fields = session.scalars(
                select(DatasetField)
                .where(DatasetField.dataset_id == dataset_id)
                .order_by(DatasetField.position)
            ).all()
            field_values = [
                {
                    "name": field.display_name,
                    "value": format_record_value(record.data.get(field.key)),
                }
                for field in fields
            ]
            dataset_name = dataset.name
            source_row_number = record.source_row_number
    except SQLAlchemyError:
        logger.exception(
            "Could not load record details for dataset %s record %s",
            dataset_id,
            record_id,
        )
        return render_page(
            request,
            "pages/record_detail.html",
            status_code=503,
            error_message="データベースに接続できないか、詳細を読み込めませんでした。",
            dataset_name="",
            source_row_number=None,
            field_values=[],
            back_url=build_search_url(request),
        )

    return render_page(
        request,
        "pages/record_detail.html",
        dataset_name=dataset_name,
        source_row_number=source_row_number,
        field_values=field_values,
        back_url=build_search_url(request),
    )


@router.get("/import", response_class=HTMLResponse, name="import_page")
def import_page(
    request: Request,
    _user: User = Depends(require_importer),
) -> HTMLResponse:
    del _user
    return render_page(request, "pages/import.html")
