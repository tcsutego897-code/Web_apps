import asyncio
import re
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import Base
from app.main import app
from app.models import Dataset, ImportHistory, ImportIssue, Record
from app.services.csv_import import import_csv, preview_csv
from app.services.import_common import ImportInputError
from tests.security_helpers import add_user, sign_in


async def request(
    method: str,
    path: str,
    *,
    data: dict[str, object] | None = None,
    files: dict[str, tuple[str, bytes, str]] | None = None,
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as client:
        await sign_in(client)
        if method.upper() == "POST":
            import_page = await client.get("/import")
            token_match = re.search(
                r'name="csrf_token" value="([^"]+)"',
                import_page.text,
            )
            assert token_match is not None
            data = {**(data or {}), "csrf_token": token_match.group(1)}
        return await client.request(method, path, data=data, files=files)


def post(
    path: str,
    *,
    data: dict[str, object] | None = None,
    files: dict[str, tuple[str, bytes, str]] | None = None,
) -> httpx.Response:
    return asyncio.run(request("POST", path, data=data, files=files))


def test_preview_supports_cp932_delimiters_header_offset_and_empty_rows() -> None:
    content = (
        "案内\r\n名前;年齢;名前\r\n\r\n  ; ;   \r\n"
        "太郎;20;Taro\r\n花子;30;Hanako\r\n"
    ).encode("cp932")

    preview = preview_csv(
        "members.csv",
        content,
        encoding="cp932",
        delimiter=";",
        header_row=2,
    )

    assert preview.record_count == 2
    assert [field.source_name for field in preview.fields] == ["名前", "年齢", "名前"]
    assert [row.row_number for row in preview.sample_rows] == [5, 6]
    assert preview.sample_rows[0].values == ["太郎", "20", "Taro"]
    assert any("重複" in warning for warning in preview.warnings)
    assert any("空行を2行" in warning for warning in preview.warnings)


def test_preview_rejects_bad_encoding_and_mismatched_column_counts() -> None:
    with pytest.raises(ImportInputError, match="文字コード"):
        preview_csv("members.csv", "名前\n太郎\n".encode("cp932"))

    with pytest.raises(ImportInputError, match="3行目") as error:
        preview_csv("members.csv", b"name,age\nAlice,20\nBob\n")
    assert error.value.source_row_number == 3

    with pytest.raises(ImportInputError, match="引用符") as error:
        preview_csv("members.csv", b'name,age\n"Alice,20\n')
    assert error.value.source_row_number == 2


def test_preview_enforces_configured_record_limit() -> None:
    with patch(
        "app.services.csv_import.get_settings",
        return_value=Settings(max_import_rows=1),
    ):
        with pytest.raises(ImportInputError, match="上限") as error:
            preview_csv("members.csv", b"name\nAlice\nBob\n")

    assert error.value.source_row_number == 3


def test_preview_rejects_empty_data_and_out_of_range_header() -> None:
    with pytest.raises(ImportInputError, match="見出しより下"):
        preview_csv("members.csv", b"name,age\n")
    with pytest.raises(ImportInputError, match="範囲外"):
        preview_csv("members.csv", b"name,age\nAlice,20\n", header_row=3)


def test_csv_upload_preview_and_confirm_persist_records_and_history(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'csv-import.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        owner_id = add_user(session).id

    try:
        with (
            patch("app.services.import_common.get_engine", return_value=engine),
            patch("app.routers.auth.get_engine", return_value=engine),
            patch("app.security.get_engine", return_value=engine),
            patch("app.routers.pages.get_engine", return_value=engine),
            patch(
                "app.services.upload_storage.STAGING_DIRECTORY",
                tmp_path / "csv-staging",
            ),
        ):
            import_page = asyncio.run(request("GET", "/import"))
            assert import_page.status_code == 200
            assert "CSVファイルを選択" in import_page.text
            assert "Shift-JIS / Windows-31J" in import_page.text

            preview_response = post(
                "/import/csv/preview",
                data={
                    "encoding": "utf-8-sig",
                    "delimiter": ",",
                    "header_row": "1",
                },
                files={
                    "upload": (
                        "people.csv",
                        b"ID,Name\n001,Alice\n002,Bob\n",
                        "text/csv",
                    )
                },
            )
            assert preview_response.status_code == 200
            assert "Alice" in preview_response.text
            token_match = re.search(
                r'name="staging_token" value="([A-Za-z0-9_-]{43})"',
                preview_response.text,
            )
            assert token_match is not None
            token = token_match.group(1)

            result_response = post(
                "/import/csv/confirm",
                data={
                    "staging_token": token,
                    "filename": "people.csv",
                    "encoding": "utf-8-sig",
                    "delimiter": ",",
                    "header_row": "1",
                    "dataset_name": "CSV People",
                    "display_names": ["識別子", "氏名"],
                },
            )
            assert result_response.status_code == 200
            assert "CSVファイルの内容" in result_response.text
            assert "CSV People" in result_response.text

        with Session(engine) as session:
            dataset = session.scalar(
                select(Dataset).where(Dataset.name == "CSV People")
            )
            assert dataset is not None
            assert dataset.owner_id == owner_id
            assert dataset.record_count == 2
            assert dataset.sheet_name == "CSV"
            records = session.scalars(
                select(Record)
                .where(Record.dataset_id == dataset.id)
                .order_by(Record.source_row_number)
            ).all()
            assert [record.data for record in records] == [
                {"field_1": "001", "field_2": "Alice"},
                {"field_1": "002", "field_2": "Bob"},
            ]
            history = session.scalar(
                select(ImportHistory).where(
                    ImportHistory.dataset_id == dataset.id
                )
            )
            assert history is not None
            assert history.status == "succeeded"
            assert history.success_count == 2
    finally:
        engine.dispose()


def test_csv_preview_row_error_is_saved_to_import_history(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'csv-row-error.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        add_user(session)

    try:
        with (
            patch("app.services.import_common.get_engine", return_value=engine),
            patch("app.routers.auth.get_engine", return_value=engine),
            patch("app.security.get_engine", return_value=engine),
            patch(
                "app.services.upload_storage.STAGING_DIRECTORY",
                tmp_path / "csv-row-error-staging",
            ),
        ):
            response = post(
                "/import/csv/preview",
                data={"header_row": "1"},
                files={
                    "upload": (
                        "broken.csv",
                        b"name,age\nAlice,20\nBob\n",
                        "text/csv",
                    )
                },
            )

        assert response.status_code == 400
        assert "3行目" in response.text
        with Session(engine) as session:
            history = session.scalar(
                select(ImportHistory).where(ImportHistory.status == "failed")
            )
            assert history is not None
            assert history.failure_count == 1
            issue = session.scalar(
                select(ImportIssue).where(
                    ImportIssue.import_history_id == history.id
                )
            )
            assert issue is not None
            assert issue.source_row_number == 3
    finally:
        engine.dispose()


def test_csv_import_rolls_back_dataset_when_record_save_fails(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'csv-rollback.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        owner_id = add_user(session).id

    from sqlalchemy import event
    from sqlalchemy.exc import SQLAlchemyError

    @event.listens_for(engine, "before_cursor_execute")
    def fail_record_insert(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        del _connection, _cursor, _parameters, _context, _executemany
        if statement.lstrip().startswith("INSERT INTO records"):
            raise SQLAlchemyError("Simulated CSV record save failure")

    try:
        with patch("app.services.import_common.get_engine", return_value=engine):
            with pytest.raises(SQLAlchemyError, match="Simulated CSV"):
                import_csv(
                    "people.csv",
                    b"name\nAlice\n",
                    encoding="utf-8-sig",
                    delimiter=",",
                    header_row=1,
                    dataset_name="Should be rolled back",
                    display_names=None,
                    owner_id=owner_id,
                )

        with Session(engine) as session:
            assert session.scalars(select(Dataset)).all() == []
            assert session.scalars(select(Record)).all() == []
            history = session.scalar(
                select(ImportHistory).where(ImportHistory.status == "failed")
            )
            assert history is not None
            assert "取り込み全体を取り消しました" in history.error_summary
    finally:
        engine.dispose()
