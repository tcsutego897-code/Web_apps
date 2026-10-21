from dataclasses import dataclass
from typing import Literal

from sqlalchemy import String, case, cast, func, select
from sqlalchemy.orm import Session

from app.models import Dataset, DatasetField, Record

MatchMode = Literal["partial", "exact"]
SortDirection = Literal["asc", "desc"]
MAX_SEARCH_CONDITIONS = 10
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100


class SearchInputError(ValueError):
    """A user-facing error caused by invalid search parameters."""


@dataclass(frozen=True)
class SearchCondition:
    field_key: str
    mode: MatchMode
    value: str


@dataclass(frozen=True)
class SearchResults:
    datasets: list[Dataset]
    selected_dataset: Dataset | None
    fields: list[DatasetField]
    records: list[Record]
    conditions: list[SearchCondition]
    total_count: int
    page: int
    page_size: int
    total_pages: int
    sort_field: str
    sort_direction: SortDirection
    searched: bool


def get_field_value_expression(database_type: str, field_key: str):
    field_value = Record.data[field_key].as_string()
    if database_type != "sqlite":
        return field_value

    json_path = f"$.{field_key}"
    json_type = func.json_type(Record.data, json_path)
    value_as_text = cast(func.json_extract(Record.data, json_path), String)
    return case(
        (json_type == "true", "true"),
        (json_type == "false", "false"),
        else_=value_as_text,
    )


def search_records(
    session: Session,
    *,
    dataset_id: int | None,
    requested_conditions: list[SearchCondition],
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    sort_field: str = "",
    sort_direction: str = "asc",
    searched: bool = False,
) -> SearchResults:
    if page < 1:
        raise SearchInputError("ページ番号は1以上を指定してください。")
    if page_size < 1 or page_size > MAX_PAGE_SIZE:
        raise SearchInputError(f"1ページの表示件数は1〜{MAX_PAGE_SIZE}件です。")
    if len(requested_conditions) > MAX_SEARCH_CONDITIONS:
        raise SearchInputError(
            f"検索条件は{MAX_SEARCH_CONDITIONS}個まで指定できます。"
        )
    if sort_direction not in ("asc", "desc"):
        raise SearchInputError("並べ替え方向が正しくありません。")

    datasets = session.scalars(
        select(Dataset).order_by(Dataset.name, Dataset.id)
    ).all()
    selected_dataset = next(
        (dataset for dataset in datasets if dataset.id == dataset_id),
        None,
    )
    if dataset_id is not None and selected_dataset is None:
        raise SearchInputError("指定したデータセットが見つかりません。")

    if selected_dataset is None:
        return SearchResults(
            datasets=datasets,
            selected_dataset=None,
            fields=[],
            records=[],
            conditions=[],
            total_count=0,
            page=1,
            page_size=page_size,
            total_pages=0,
            sort_field="",
            sort_direction="asc",
            searched=searched,
        )

    fields = session.scalars(
        select(DatasetField)
        .where(DatasetField.dataset_id == selected_dataset.id)
        .order_by(DatasetField.position)
    ).all()
    fields_by_key = {field.key: field for field in fields}

    conditions = requested_conditions
    for condition in conditions:
        if condition.field_key not in fields_by_key:
            raise SearchInputError("検索対象に、このデータセットにない項目が含まれています。")
        if not fields_by_key[condition.field_key].is_searchable:
            raise SearchInputError("この項目は検索対象に設定されていません。")
        if condition.mode not in ("partial", "exact"):
            raise SearchInputError("検索方法が正しくありません。")
        if not condition.value.strip():
            raise SearchInputError("検索語を入力してください。")
        if len(condition.value) > 500:
            raise SearchInputError("検索語は500文字以内で入力してください。")

    if sort_field and sort_field not in fields_by_key:
        raise SearchInputError("並べ替え対象に、このデータセットにない項目が含まれています。")

    query = select(Record).where(Record.dataset_id == selected_dataset.id)
    for condition in conditions:
        field_value = get_field_value_expression(
            session.bind.dialect.name if session.bind is not None else "",
            condition.field_key,
        )
        if condition.mode == "exact":
            query = query.where(field_value == condition.value)
        else:
            query = query.where(field_value.icontains(condition.value, autoescape=True))

    total_count = session.scalar(
        select(func.count()).select_from(query.order_by(None).subquery())
    ) or 0
    total_pages = (total_count + page_size - 1) // page_size
    current_page = min(page, max(total_pages, 1))

    if sort_field:
        sort_expression = get_field_value_expression(
            session.bind.dialect.name if session.bind is not None else "",
            sort_field,
        )
        if sort_direction == "desc":
            query = query.order_by(sort_expression.is_(None), sort_expression.desc())
        else:
            query = query.order_by(sort_expression.is_(None), sort_expression.asc())
        query = query.order_by(Record.source_row_number, Record.id)
    else:
        query = query.order_by(Record.source_row_number, Record.id)

    records = session.scalars(
        query.limit(page_size).offset((current_page - 1) * page_size)
    ).all()

    return SearchResults(
        datasets=datasets,
        selected_dataset=selected_dataset,
        fields=fields,
        records=records,
        conditions=conditions,
        total_count=total_count,
        page=current_page,
        page_size=page_size,
        total_pages=total_pages,
        sort_field=sort_field,
        sort_direction=sort_direction,
        searched=searched,
    )
