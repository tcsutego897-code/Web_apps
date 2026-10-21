import logging
from datetime import date, datetime, time
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_engine
from app.models import Dataset, DatasetField, Record
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
    "match_mode",
    "value",
    "page",
    "page_size",
    "sort_field",
    "sort_direction",
}
SEARCH_LIST_FIELD_LIMIT = 5


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
        context={"app_name": get_settings().app_name, **context},
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
def datasets_page(request: Request) -> HTMLResponse:
    try:
        with Session(get_engine()) as session:
            datasets = session.scalars(
                select(Dataset).order_by(Dataset.imported_at.desc(), Dataset.id.desc())
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

    return render_page(request, "pages/datasets.html", datasets=datasets)


@router.get("/search", response_class=HTMLResponse, name="search")
def search_page(
    request: Request,
    dataset_id: int | None = None,
    field_key: list[str] = Query(default=[]),
    match_mode: list[str] = Query(default=[]),
    value: list[str] = Query(default=[]),
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    sort_field: str = "",
    sort_direction: str = "asc",
) -> HTMLResponse:
    error_message = None
    results = None
    condition_rows = []

    try:
        if not (len(field_key) == len(match_mode) == len(value)):
            raise SearchInputError("検索条件の項目・方法・検索語をそろえてください。")
        if len(field_key) > MAX_SEARCH_CONDITIONS:
            raise SearchInputError(
                f"検索条件は{MAX_SEARCH_CONDITIONS}個まで指定できます。"
            )

        conditions = []
        for selected_field, selected_mode, search_value in zip(
            field_key,
            match_mode,
            value,
        ):
            if not selected_field and not search_value.strip():
                continue
            if not selected_field or not search_value.strip():
                raise SearchInputError("各検索条件の項目と検索語を入力してください。")
            conditions.append(
                SearchCondition(
                    field_key=selected_field,
                    mode=selected_mode,
                    value=search_value,
                )
            )

        with Session(get_engine()) as session:
            results = search_records(
                session,
                dataset_id=dataset_id,
                requested_conditions=conditions,
                page=page,
                page_size=page_size,
                sort_field=sort_field,
                sort_direction=sort_direction,
                searched=bool(dataset_id),
            )

        if results.selected_dataset is not None:
            condition_rows = [
                {
                    "field_key": condition.field_key,
                    "match_mode": condition.mode,
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
                    select(Dataset).order_by(Dataset.name, Dataset.id)
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
                    ("match_mode", condition.mode),
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
) -> HTMLResponse:
    try:
        with Session(get_engine()) as session:
            dataset = session.get(Dataset, dataset_id)
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
def import_page(request: Request) -> HTMLResponse:
    return render_page(request, "pages/import.html")


@router.get("/login", response_class=HTMLResponse, name="login")
def login_page(request: Request) -> HTMLResponse:
    return render_page(request, "pages/login.html")
