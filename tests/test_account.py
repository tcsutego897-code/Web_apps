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
from app.models import Dataset, DatasetField, ImportHistory, ImportIssue, Record, User
from app.security import verify_password
from tests.security_helpers import TEST_EMAIL, TEST_PASSWORD, add_user, sign_in

NEW_PASSWORD = "Another-secure-test-password"


@pytest.fixture
def account_database(tmp_path: Path) -> Iterator[tuple[Engine, int, int]]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'account.db'}")

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
            name="Owner dataset",
            source_filename="owner.xlsx",
            sheet_name="Records",
            record_count=1,
            fields=[
                DatasetField(
                    key="field_1",
                    source_name="Name",
                    display_name="Name",
                    position=0,
                )
            ],
            records=[
                Record(
                    source_row_number=2,
                    data={"field_1": "Owner record"},
                )
            ],
        )
        dataset.import_history.append(
            ImportHistory(
                owner_id=owner.id,
                source_filename="owner.xlsx",
                sheet_name="Records",
                status="succeeded",
                success_count=1,
            )
        )
        failed_history = ImportHistory(
            owner_id=owner.id,
            source_filename="owner-failed.xlsx",
            sheet_name="Records",
            status="failed",
            failure_count=1,
            error_summary="Import failed.",
            issues=[ImportIssue(source_row_number=4, message="Invalid row.")],
        )
        other_dataset = Dataset(
            owner_id=other_user.id,
            name="Other user's dataset",
            source_filename="other.xlsx",
            sheet_name="Records",
            record_count=1,
            records=[
                Record(
                    source_row_number=2,
                    data={"field_1": "Other user's record"},
                )
            ],
        )
        other_dataset.import_history.append(
            ImportHistory(
                owner_id=other_user.id,
                source_filename="other.xlsx",
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
    password: str = TEST_PASSWORD,
    form_data: dict[str, str] | None = None,
    authenticate: bool = True,
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as client:
        if authenticate:
            await sign_in(client, email=email, password=password)
        if method == "GET":
            return await client.get(path)

        account_page = await client.get("/account")
        token_match = re.search(
            r'name="csrf_token" value="([^"]+)"',
            account_page.text,
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
    password: str = TEST_PASSWORD,
    form_data: dict[str, str] | None = None,
    authenticate: bool = True,
) -> httpx.Response:
    return asyncio.run(
        _request(
            path,
            method=method,
            email=email,
            password=password,
            form_data=form_data,
            authenticate=authenticate,
        )
    )


def test_account_page_requires_login_and_displays_forms(
    account_database: tuple[Engine, int, int],
) -> None:
    engine, _dataset_id, _other_dataset_id = account_database
    with (
        patch("app.routers.auth.get_engine", return_value=engine),
        patch("app.routers.account.get_engine", return_value=engine),
        patch("app.security.get_engine", return_value=engine),
    ):
        unauthenticated = request("/account", authenticate=False)
        page = request("/account")

    assert unauthenticated.status_code == 303
    assert unauthenticated.headers["location"].startswith("/login?next=")
    assert page.status_code == 200
    assert TEST_EMAIL in page.text
    assert 'action="http://testserver/account/email"' in page.text
    assert 'action="http://testserver/account/password"' in page.text
    assert 'action="http://testserver/account/delete"' in page.text
    assert "メール送信機能を設定するまで利用できません" in page.text


def test_email_change_requires_confirmation_and_current_password(
    account_database: tuple[Engine, int, int],
) -> None:
    engine, _dataset_id, _other_dataset_id = account_database
    with Session(engine) as session:
        other_user = session.scalar(
            select(User).where(User.email == "other@example.test")
        )
        assert other_user is not None
        existing_email = other_user.email

    with (
        patch("app.routers.auth.get_engine", return_value=engine),
        patch("app.routers.account.get_engine", return_value=engine),
        patch("app.security.get_engine", return_value=engine),
    ):
        mismatch = request(
            "/account/email",
            method="POST",
            form_data={
                "new_email": "changed@example.test",
                "email_confirmation": "different@example.test",
                "current_password": TEST_PASSWORD,
            },
        )
        duplicate = request(
            "/account/email",
            method="POST",
            form_data={
                "new_email": existing_email,
                "email_confirmation": existing_email,
                "current_password": TEST_PASSWORD,
            },
        )
        wrong_password = request(
            "/account/email",
            method="POST",
            form_data={
                "new_email": "changed@example.test",
                "email_confirmation": "changed@example.test",
                "current_password": "not-the-current-password",
            },
        )

    assert mismatch.status_code == 400
    assert "確認用メールアドレスが一致しません" in mismatch.text
    assert duplicate.status_code == 409
    assert "既に登録されています" in duplicate.text
    assert wrong_password.status_code == 400
    assert "現在のパスワードを確認してください" in wrong_password.text
    with Session(engine) as session:
        assert session.scalar(
            select(User).where(User.email == TEST_EMAIL)
        ) is not None


def test_email_change_normalizes_and_saves_new_email(
    account_database: tuple[Engine, int, int],
) -> None:
    engine, _dataset_id, _other_dataset_id = account_database
    with (
        patch("app.routers.auth.get_engine", return_value=engine),
        patch("app.routers.account.get_engine", return_value=engine),
        patch("app.security.get_engine", return_value=engine),
    ):
        response = request(
            "/account/email",
            method="POST",
            form_data={
                "new_email": "  New-Address@Example.Test ",
                "email_confirmation": "new-address@example.test",
                "current_password": TEST_PASSWORD,
            },
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/account?email_updated=1"
    with Session(engine) as session:
        user = session.scalar(
            select(User).where(User.email == "new-address@example.test")
        )
        assert user is not None
        assert user.password_hash.startswith("pbkdf2_sha256$")


def test_password_change_checks_old_password_and_confirmation(
    account_database: tuple[Engine, int, int],
) -> None:
    engine, _dataset_id, _other_dataset_id = account_database
    with (
        patch("app.routers.auth.get_engine", return_value=engine),
        patch("app.routers.account.get_engine", return_value=engine),
        patch("app.security.get_engine", return_value=engine),
    ):
        mismatch = request(
            "/account/password",
            method="POST",
            form_data={
                "current_password": TEST_PASSWORD,
                "new_password": NEW_PASSWORD,
                "password_confirmation": "different-password-value",
            },
        )
        wrong_current_password = request(
            "/account/password",
            method="POST",
            form_data={
                "current_password": "incorrect-current-password",
                "new_password": NEW_PASSWORD,
                "password_confirmation": NEW_PASSWORD,
            },
        )
        too_short = request(
            "/account/password",
            method="POST",
            form_data={
                "current_password": TEST_PASSWORD,
                "new_password": "too-short",
                "password_confirmation": "too-short",
            },
        )

    assert mismatch.status_code == 400
    assert "新しいパスワードが一致しません" in mismatch.text
    assert wrong_current_password.status_code == 400
    assert "現在のパスワードを確認してください" in wrong_current_password.text
    assert too_short.status_code == 400
    assert "12〜1024文字" in too_short.text


def test_password_change_updates_hash_and_keeps_user_signed_in(
    account_database: tuple[Engine, int, int],
) -> None:
    engine, _dataset_id, _other_dataset_id = account_database

    async def change_and_load_account() -> tuple[httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=False,
        ) as client:
            await sign_in(client)
            account_page = await client.get("/account")
            token_match = re.search(
                r'name="csrf_token" value="([^"]+)"',
                account_page.text,
            )
            assert token_match is not None
            response = await client.post(
                "/account/password",
                data={
                    "current_password": TEST_PASSWORD,
                    "new_password": NEW_PASSWORD,
                    "password_confirmation": NEW_PASSWORD,
                    "csrf_token": token_match.group(1),
                },
            )
            updated_page = await client.get(response.headers["location"])
            return response, updated_page

    with (
        patch("app.routers.auth.get_engine", return_value=engine),
        patch("app.routers.account.get_engine", return_value=engine),
        patch("app.security.get_engine", return_value=engine),
    ):
        response, updated_page = asyncio.run(change_and_load_account())
        new_password_login = request("/account", password=NEW_PASSWORD)

    assert response.status_code == 303
    assert updated_page.status_code == 200
    assert "パスワードを変更しました" in updated_page.text
    assert new_password_login.status_code == 200
    with Session(engine) as session:
        user = session.scalar(select(User).where(User.email == TEST_EMAIL))
        assert user is not None
        assert verify_password(NEW_PASSWORD, user.password_hash)
        assert not verify_password(TEST_PASSWORD, user.password_hash)


def test_account_deletion_removes_owned_data_and_preserves_other_users(
    account_database: tuple[Engine, int, int],
) -> None:
    engine, dataset_id, other_dataset_id = account_database
    with (
        patch("app.routers.auth.get_engine", return_value=engine),
        patch("app.routers.account.get_engine", return_value=engine),
        patch("app.security.get_engine", return_value=engine),
    ):
        response = request(
            "/account/delete",
            method="POST",
            form_data={
                "confirmation_email": TEST_EMAIL,
                "current_password": TEST_PASSWORD,
                "confirm_delete": "yes",
            },
        )
        login_page = request("/login?account_deleted=1", authenticate=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login?account_deleted=1"
    assert login_page.status_code == 200
    assert "アカウントと関連データを削除しました" in login_page.text
    with Session(engine) as session:
        assert session.scalar(
            select(User).where(User.email == TEST_EMAIL)
        ) is None
        assert session.get(Dataset, dataset_id) is None
        assert session.scalar(
            select(Record).where(Record.dataset_id == dataset_id)
        ) is None
        assert session.scalar(
            select(DatasetField).where(DatasetField.dataset_id == dataset_id)
        ) is None
        remaining_history = session.scalar(
            select(ImportHistory.source_filename).where(
                ImportHistory.owner_id.is_not(None)
            )
        )
        assert remaining_history == "other.xlsx"
        assert session.scalar(
            select(ImportIssue).where(ImportIssue.message == "Invalid row.")
        ) is None
        assert session.get(Dataset, other_dataset_id) is not None
        assert session.scalar(
            select(User).where(User.email == "other@example.test")
        ) is not None


@pytest.mark.parametrize(
    ("form_data", "expected_error"),
    [
        (
            {
                "confirmation_email": TEST_EMAIL,
                "current_password": TEST_PASSWORD,
            },
            "確認欄にチェックしてください",
        ),
        (
            {
                "confirmation_email": "wrong@example.test",
                "current_password": TEST_PASSWORD,
                "confirm_delete": "yes",
            },
            "確認用メールアドレスが一致しません",
        ),
        (
            {
                "confirmation_email": TEST_EMAIL,
                "current_password": "incorrect-current-password",
                "confirm_delete": "yes",
            },
            "現在のパスワードを確認してください",
        ),
    ],
)
def test_account_deletion_requires_explicit_confirmation_and_password(
    account_database: tuple[Engine, int, int],
    form_data: dict[str, str],
    expected_error: str,
) -> None:
    engine, _dataset_id, _other_dataset_id = account_database
    with (
        patch("app.routers.auth.get_engine", return_value=engine),
        patch("app.routers.account.get_engine", return_value=engine),
        patch("app.security.get_engine", return_value=engine),
    ):
        response = request(
            "/account/delete",
            method="POST",
            form_data=form_data,
        )

    assert response.status_code == 400
    assert expected_error in response.text
    with Session(engine) as session:
        assert session.scalar(
            select(User).where(User.email == TEST_EMAIL)
        ) is not None
