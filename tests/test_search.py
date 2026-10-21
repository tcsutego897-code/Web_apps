import asyncio
from collections.abc import Iterator
from pathlib import Path
from sqlite3 import Connection
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy import event
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.database import Base
from app.main import app
from app.models import Dataset, DatasetField, Record
from app.services.search import (
    SearchCondition,
    SearchInputError,
    search_records,
)


@pytest.fixture
def search_database(tmp_path: Path) -> Iterator[tuple[Engine, int]]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'search.db'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection: Connection, _record: object) -> None:
        del _record
        connection.execute("PRAGMA foreign_keys = ON")

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        people = Dataset(
            name="People",
            source_filename="people.xlsx",
            sheet_name="People",
            record_count=3,
            fields=[
                DatasetField(
                    key="field_1",
                    source_name="Name",
                    display_name="名前",
                    position=0,
                ),
                DatasetField(
                    key="field_2",
                    source_name="City",
                    display_name="市区町村",
                    position=1,
                ),
                DatasetField(
                    key="field_3",
                    source_name="Active",
                    display_name="有効",
                    position=2,
                ),
                DatasetField(
                    key="field_4",
                    source_name="Notes",
                    display_name="メモ",
                    position=3,
                ),
                DatasetField(
                    key="field_5",
                    source_name="Updated",
                    display_name="更新日時",
                    position=4,
                ),
                DatasetField(
                    key="field_6",
                    source_name="Private note",
                    display_name="詳細メモ",
                    position=5,
                ),
                DatasetField(
                    key="field_7",
                    source_name="Optional",
                    display_name="任意項目",
                    position=6,
                ),
                DatasetField(
                    key="field_8",
                    source_name="Updated time",
                    display_name="時刻",
                    position=7,
                ),
            ],
            records=[
                Record(
                    source_row_number=2,
                    data={
                        "field_1": "Alice",
                        "field_2": "Tokyo",
                        "field_3": True,
                        "field_4": "First line\n<script>alert(1)</script>",
                        "field_5": "2025-06-07T08:09:10",
                        "field_6": "Only shown in detail",
                        "field_7": None,
                        "field_8": "09:02:30.125000",
                    },
                ),
                Record(
                    source_row_number=3,
                    data={
                        "field_1": "Alicia",
                        "field_2": "Osaka",
                        "field_3": False,
                    },
                ),
                Record(
                    source_row_number=4,
                    data={
                        "field_1": "Bob 100%_done",
                        "field_2": "Tokyo",
                        "field_3": True,
                    },
                ),
            ],
        )
        orders = Dataset(
            name="Orders",
            source_filename="orders.xlsx",
            sheet_name="Orders",
            record_count=1,
            fields=[
                DatasetField(
                    key="field_1",
                    source_name="Total",
                    display_name="合計",
                    position=0,
                )
            ],
            records=[
                Record(source_row_number=2, data={"field_1": 125.5}),
            ],
        )
        session.add_all([people, orders])
        session.commit()
        people_id = people.id
    yield engine, people_id
    engine.dispose()


def test_search_matches_exact_partial_and_and_conditions(
    search_database: tuple[object, int],
) -> None:
    engine, dataset_id = search_database
    with Session(engine) as session:
        partial = search_records(
            session,
            dataset_id=dataset_id,
            requested_conditions=[
                SearchCondition("field_1", "partial", "ali"),
                SearchCondition("field_2", "exact", "Osaka"),
            ],
        )
        assert partial.total_count == 1
        assert partial.records[0].data["field_1"] == "Alicia"

        exact = search_records(
            session,
            dataset_id=dataset_id,
            requested_conditions=[SearchCondition("field_1", "exact", "Alice")],
        )
        assert exact.total_count == 1
        assert exact.records[0].data["field_1"] == "Alice"


def test_partial_search_treats_wildcards_and_sql_as_literal_text(
    search_database: tuple[object, int],
) -> None:
    engine, dataset_id = search_database
    with Session(engine) as session:
        results = search_records(
            session,
            dataset_id=dataset_id,
            requested_conditions=[
                SearchCondition("field_1", "partial", "100%_done")
            ],
        )

    assert results.total_count == 1
    assert results.records[0].data["field_1"] == "Bob 100%_done"

    with Session(engine) as session:
        injection_text = search_records(
            session,
            dataset_id=dataset_id,
            requested_conditions=[
                SearchCondition("field_1", "partial", "' OR 1=1 --")
            ],
        )
    assert injection_text.total_count == 0


def test_search_handles_json_numbers_and_booleans(
    search_database: tuple[object, int],
) -> None:
    engine, dataset_id = search_database
    with Session(engine) as session:
        true_results = search_records(
            session,
            dataset_id=dataset_id,
            requested_conditions=[SearchCondition("field_3", "exact", "true")],
        )
        number_results = search_records(
            session,
            dataset_id=dataset_id + 1,
            requested_conditions=[SearchCondition("field_1", "exact", "125.5")],
        )

    assert true_results.total_count == 2
    assert number_results.total_count == 1


def test_search_sorts_and_paginates_with_stable_page_urls(
    search_database: tuple[object, int],
) -> None:
    engine, dataset_id = search_database
    with Session(engine) as session:
        first_page = search_records(
            session,
            dataset_id=dataset_id,
            requested_conditions=[],
            page=1,
            page_size=2,
            sort_field="field_1",
            sort_direction="desc",
        )
        second_page = search_records(
            session,
            dataset_id=dataset_id,
            requested_conditions=[],
            page=2,
            page_size=2,
            sort_field="field_1",
            sort_direction="desc",
        )

    assert first_page.total_count == 3
    assert first_page.total_pages == 2
    assert [record.data["field_1"] for record in first_page.records] == [
        "Bob 100%_done",
        "Alicia",
    ]
    assert second_page.page == 2
    assert [record.data["field_1"] for record in second_page.records] == ["Alice"]


@pytest.mark.parametrize(
    ("field_key", "mode", "value"),
    [
        ("missing_field", "exact", "x"),
        ("field_1); DROP TABLE records;--", "exact", "x"),
        ("field_3", "contains", "true"),
    ],
)
def test_invalid_fields_and_unsearchable_fields_are_rejected(
    search_database: tuple[Engine, int],
    field_key: str,
    mode: str,
    value: str,
) -> None:
    engine, dataset_id = search_database
    with Session(engine) as session, pytest.raises(SearchInputError):
        search_records(
            session,
            dataset_id=dataset_id,
            requested_conditions=[SearchCondition(field_key, mode, value)],
        )


@pytest.mark.parametrize(
    ("page", "page_size", "sort_direction"),
    [
        (0, 25, "asc"),
        (1, 101, "asc"),
        (1, 25, "sideways"),
    ],
)
def test_invalid_pagination_and_sort_parameters_are_rejected(
    search_database: tuple[Engine, int],
    page: int,
    page_size: int,
    sort_direction: str,
) -> None:
    engine, dataset_id = search_database
    with Session(engine) as session, pytest.raises(SearchInputError):
        search_records(
            session,
            dataset_id=dataset_id,
            requested_conditions=[],
            page=page,
            page_size=page_size,
            sort_direction=sort_direction,
        )


async def _get_search(path: str, parameters: list[tuple[str, str]]) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        return await client.get(path, params=parameters)


def get_search(path: str, parameters: list[tuple[str, str]]) -> httpx.Response:
    return asyncio.run(_get_search(path, parameters))


def test_search_page_renders_filters_results_pagination_and_clear_link(
    search_database: tuple[Engine, int],
) -> None:
    engine, dataset_id = search_database
    with patch("app.routers.pages.get_engine", return_value=engine):
        response = get_search(
            "/search",
            [
                ("dataset_id", str(dataset_id)),
                ("field_key", "field_1"),
                ("match_mode", "partial"),
                ("value", "ali"),
                ("page_size", "1"),
            ],
        )

    assert response.status_code == 200
    assert "Alice" in response.text
    assert "2 件" in response.text
    assert "次へ" in response.text
    assert "/search" in response.text
    assert "条件をクリア" in response.text


def test_search_page_rejects_unregistered_search_key(
    search_database: tuple[Engine, int],
) -> None:
    engine, dataset_id = search_database
    with patch("app.routers.pages.get_engine", return_value=engine):
        response = get_search(
            "/search",
            [
                ("dataset_id", str(dataset_id)),
                ("field_key", "field_1) OR 1=1 --"),
                ("match_mode", "exact"),
                ("value", "x"),
            ],
        )

    assert response.status_code == 400
    assert "このデータセットにない項目" in response.text


def test_search_list_shows_representative_fields_and_links_to_details(
    search_database: tuple[Engine, int],
) -> None:
    engine, dataset_id = search_database
    with patch("app.routers.pages.get_engine", return_value=engine):
        response = get_search(
            "/search",
            [("dataset_id", str(dataset_id)), ("page_size", "1")],
        )

    assert response.status_code == 200
    assert "詳細を見る" in response.text
    assert f"/datasets/{dataset_id}/records/" in response.text
    assert "Only shown in detail" not in response.text


def test_record_detail_shows_every_field_and_preserves_search_context(
    search_database: tuple[Engine, int],
) -> None:
    engine, dataset_id = search_database
    with Session(engine) as session:
        record = session.scalar(
            select(Record).where(
                Record.dataset_id == dataset_id,
                Record.source_row_number == 2,
            )
        )
        assert record is not None
        record_id = record.id

    with patch("app.routers.pages.get_engine", return_value=engine):
        response = get_search(
            f"/datasets/{dataset_id}/records/{record_id}",
            [
                ("dataset_id", str(dataset_id)),
                ("field_key", "field_1"),
                ("match_mode", "partial"),
                ("value", "ali"),
                ("page", "2"),
                ("page_size", "10"),
                ("sort_field", "field_1"),
                ("sort_direction", "desc"),
                ("untrusted", "must-not-be-forwarded"),
            ],
        )

    assert response.status_code == 200
    assert "詳細メモ" in response.text
    assert "Only shown in detail" in response.text
    assert "任意項目" in response.text
    assert "時刻" in response.text
    assert "—" in response.text
    assert "2025-06-07 08:09:10" in response.text
    assert "09:02:30.125000" in response.text
    assert "First line\n&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
    assert "page=2" in response.text
    assert "untrusted" not in response.text


def test_record_detail_cannot_open_record_from_another_dataset(
    search_database: tuple[Engine, int],
) -> None:
    engine, people_dataset_id = search_database
    with Session(engine) as session:
        order_record = session.scalar(
            select(Record).where(Record.dataset_id != people_dataset_id)
        )
        assert order_record is not None
        order_record_id = order_record.id

    with patch("app.routers.pages.get_engine", return_value=engine):
        response = get_search(
            f"/datasets/{people_dataset_id}/records/{order_record_id}",
            [],
        )

    assert response.status_code == 404
