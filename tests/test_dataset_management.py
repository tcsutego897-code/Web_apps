import asyncio
import re
from collections.abc import Iterator
from pathlib import Path
from sqlite3 import Connection
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.database import Base
from app.main import app
from app.models import Dataset, DatasetField, ImportHistory, ImportIssue, Record
from tests.security_helpers import TEST_EMAIL, add_user, sign_in


@pytest.fixture
def management_database(
    tmp_path: Path,
) -> Iterator[tuple[Engine, int, int]]:
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'dataset-management.db'}"
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection: Connection, _record: object) -> None:
        del _record
        connection.execute("PRAGMA foreign_keys = ON")

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        owner = add_user(session)
        other_user = add_user(session, email="other@example.test")
        dataset = Dataset(
            owner_id=owner.id,
            name="資料データ",
            source_filename="library.xlsx",
            sheet_name="Records",
            record_count=1,
            fields=[
                DatasetField(
                    key="field_1",
                    source_name="Name",
                    display_name="資料名",
                    position=0,
                )
            ],
            records=[
                Record(
                    source_row_number=2,
                    data={"field_1": "Sample record"},
                )
            ],
        )
        dataset.import_history.append(
            ImportHistory(
                owner_id=owner.id,
                source_filename="library.xlsx",
                sheet_name="Records",
                status="succeeded",
                success_count=1,
            )
        )
        failed_history = ImportHistory(
            owner_id=owner.id,
            source_filename="broken.xlsx",
            sheet_name="Records",
            status="failed",
            failure_count=1,
            error_summary="Excelファイルを読み込めませんでした。",
            issues=[
                ImportIssue(
                    source_row_number=3,
                    message="3行目の値を確認してください。",
                )
            ],
        )
        other_dataset = Dataset(
            owner_id=other_user.id,
            name="他の利用者の資料",
            source_filename="private.xlsx",
            sheet_name="Records",
        )
        other_dataset.import_history.append(
            ImportHistory(
                owner_id=other_user.id,
                source_filename="private.xlsx",
                sheet_name="Records",
                status="succeeded",
                success_count=1,
            )
        )
        session.add_all([dataset, failed_history, other_dataset])
        session.commit()
        dataset_id = dataset.id
        other_dataset_id = other_dataset.id

    yield engine, dataset_id, other_dataset_id
    engine.dispose()


async def _request(
    path: str,
    *,
    method: str = "GET",
    email: str = TEST_EMAIL,
    form_data: dict[str, str] | None = None,
    csrf_page: str | None = None,
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as client:
        await sign_in(client, email=email)
        if method == "GET":
            return await client.get(path)

        page = await client.get(csrf_page or "/")
        token_match = re.search(
            r'name="csrf_token" value="([^"]+)"',
            page.text,
        )
        assert token_match is not None
        data = dict(form_data or {})
        data["csrf_token"] = token_match.group(1)
        return await client.post(path, data=data)


def request(
    path: str,
    *,
    method: str = "GET",
    email: str = TEST_EMAIL,
    form_data: dict[str, str] | None = None,
    csrf_page: str | None = None,
) -> httpx.Response:
    return asyncio.run(
        _request(
            path,
            method=method,
            email=email,
            form_data=form_data,
            csrf_page=csrf_page,
        )
    )


def test_dataset_list_links_to_management_and_import_history(
    management_database: tuple[Engine, int, int],
) -> None:
    engine, dataset_id, _other_dataset_id = management_database
    with (
        patch("app.routers.auth.get_engine", return_value=engine),
        patch("app.security.get_engine", return_value=engine),
        patch("app.routers.pages.get_engine", return_value=engine),
    ):
        response = request("/")

    assert response.status_code == 200
    assert f"/datasets/{dataset_id}/manage" in response.text
    assert "/imports/history" in response.text


def test_dataset_management_page_shows_only_its_history(
    management_database: tuple[Engine, int, int],
) -> None:
    engine, dataset_id, _other_dataset_id = management_database
    with (
        patch("app.routers.auth.get_engine", return_value=engine),
        patch("app.security.get_engine", return_value=engine),
        patch("app.routers.pages.get_engine", return_value=engine),
    ):
        response = request(f"/datasets/{dataset_id}/manage")

    assert response.status_code == 200
    assert "資料データ" in response.text
    assert "library.xlsx" in response.text
    assert "成功" in response.text
    assert "他の利用者の資料" not in response.text
    assert 'name="confirm_delete"' in response.text
    assert 'name="csrf_token"' in response.text


def test_import_history_shows_owned_success_and_failed_imports(
    management_database: tuple[Engine, int, int],
) -> None:
    engine, dataset_id, _other_dataset_id = management_database
    with (
        patch("app.routers.auth.get_engine", return_value=engine),
        patch("app.security.get_engine", return_value=engine),
        patch("app.routers.pages.get_engine", return_value=engine),
    ):
        response = request("/imports/history")

    assert response.status_code == 200
    assert "library.xlsx" in response.text
    assert "broken.xlsx" in response.text
    assert "Excelファイルを読み込めませんでした。" in response.text
    assert "未作成または削除済み" in response.text
    assert "private.xlsx" not in response.text
    assert f"/datasets/{dataset_id}/manage" in response.text


def test_import_history_can_be_filtered_to_an_owned_dataset(
    management_database: tuple[Engine, int, int],
) -> None:
    engine, dataset_id, other_dataset_id = management_database
    with (
        patch("app.routers.auth.get_engine", return_value=engine),
        patch("app.security.get_engine", return_value=engine),
        patch("app.routers.pages.get_engine", return_value=engine),
    ):
        response = request(f"/imports/history?dataset_id={dataset_id}")
        inaccessible_response = request(
            f"/imports/history?dataset_id={other_dataset_id}"
        )

    assert response.status_code == 200
    assert "library.xlsx" in response.text
    assert "broken.xlsx" not in response.text
    assert inaccessible_response.status_code == 404


def test_rename_dataset_trims_and_saves_name(
    management_database: tuple[Engine, int, int],
) -> None:
    engine, dataset_id, _other_dataset_id = management_database
    with (
        patch("app.routers.auth.get_engine", return_value=engine),
        patch("app.security.get_engine", return_value=engine),
        patch("app.routers.pages.get_engine", return_value=engine),
    ):
        response = request(
            f"/datasets/{dataset_id}/rename",
            method="POST",
            csrf_page=f"/datasets/{dataset_id}/manage",
            form_data={"dataset_name": "  新しい名前  "},
        )

    assert response.status_code == 303
    assert response.headers["location"] == f"/datasets/{dataset_id}/manage?updated=1"
    with Session(engine) as session:
        dataset = session.get(Dataset, dataset_id)
        assert dataset is not None
        assert dataset.name == "新しい名前"


@pytest.mark.parametrize(
    ("dataset_name", "expected_error"),
    [
        ("   ", "データセット名を入力してください。"),
        ("x" * 256, "データセット名は255文字以内にしてください。"),
    ],
)
def test_rename_dataset_rejects_invalid_names(
    management_database: tuple[Engine, int, int],
    dataset_name: str,
    expected_error: str,
) -> None:
    engine, dataset_id, _other_dataset_id = management_database
    with (
        patch("app.routers.auth.get_engine", return_value=engine),
        patch("app.security.get_engine", return_value=engine),
        patch("app.routers.pages.get_engine", return_value=engine),
    ):
        response = request(
            f"/datasets/{dataset_id}/rename",
            method="POST",
            csrf_page=f"/datasets/{dataset_id}/manage",
            form_data={"dataset_name": dataset_name},
        )

    assert response.status_code == 400
    assert expected_error in response.text
    with Session(engine) as session:
        dataset = session.get(Dataset, dataset_id)
        assert dataset is not None
        assert dataset.name == "資料データ"


def test_dataset_delete_requires_confirmation_and_keeps_history(
    management_database: tuple[Engine, int, int],
) -> None:
    engine, dataset_id, _other_dataset_id = management_database
    with (
        patch("app.routers.auth.get_engine", return_value=engine),
        patch("app.security.get_engine", return_value=engine),
        patch("app.routers.pages.get_engine", return_value=engine),
    ):
        unconfirmed_response = request(
            f"/datasets/{dataset_id}/delete",
            method="POST",
            csrf_page=f"/datasets/{dataset_id}/manage",
        )
        response = request(
            f"/datasets/{dataset_id}/delete",
            method="POST",
            csrf_page=f"/datasets/{dataset_id}/manage",
            form_data={"confirm_delete": "yes"},
        )
        list_response = request("/?deleted=1")

    assert unconfirmed_response.status_code == 400
    assert "確認欄にチェック" in unconfirmed_response.text
    assert response.status_code == 303
    assert response.headers["location"] == "/?deleted=1"
    assert list_response.status_code == 200
    assert "データセットを削除しました。" in list_response.text

    with Session(engine) as session:
        assert session.get(Dataset, dataset_id) is None
        history = session.scalar(
            select(ImportHistory).where(
                ImportHistory.source_filename == "library.xlsx",
            )
        )
        assert history is not None
        assert history.dataset_id is None
        assert session.scalar(
            select(Record).where(Record.dataset_id == dataset_id)
        ) is None
        assert session.scalar(
            select(DatasetField).where(DatasetField.dataset_id == dataset_id)
        ) is None


def test_user_cannot_manage_or_delete_another_users_dataset(
    management_database: tuple[Engine, int, int],
) -> None:
    engine, _dataset_id, other_dataset_id = management_database
    with (
        patch("app.routers.auth.get_engine", return_value=engine),
        patch("app.security.get_engine", return_value=engine),
        patch("app.routers.pages.get_engine", return_value=engine),
    ):
        management_response = request(f"/datasets/{other_dataset_id}/manage")
        rename_response = request(
            f"/datasets/{other_dataset_id}/rename",
            method="POST",
            csrf_page="/",
            form_data={"dataset_name": "不正な変更"},
        )
        delete_response = request(
            f"/datasets/{other_dataset_id}/delete",
            method="POST",
            csrf_page="/",
            form_data={"confirm_delete": "yes"},
        )

    assert management_response.status_code == 404
    assert rename_response.status_code == 404
    assert delete_response.status_code == 404
    with Session(engine) as session:
        assert session.get(Dataset, other_dataset_id) is not None
