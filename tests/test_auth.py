import asyncio
import re
from collections.abc import Iterator
from pathlib import Path
from sqlite3 import Connection
from unittest.mock import MagicMock, Mock, patch

import httpx
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.cli import create_user
from app.database import Base, DatabaseConfigurationError
from app.main import app
from app.models import Dataset, DatasetField, Record, User
from app.security import hash_password, verify_password
from tests.security_helpers import (
    TEST_EMAIL,
    TEST_PASSWORD,
    TEST_PASSWORD_HASH,
    add_user,
    sign_in,
)


@pytest.fixture
def auth_database(tmp_path: Path) -> Iterator[Engine]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'auth.db'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection: Connection, _record: object) -> None:
        del _record
        connection.execute("PRAGMA foreign_keys = ON")

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        importer = add_user(session)
        add_user(session, email="viewer@example.test", role="viewer")
        session.add(
            User(
                email="inactive@example.test",
                password_hash=TEST_PASSWORD_HASH,
                role="importer",
                is_active=False,
            )
        )
        session.add(
            Dataset(
                owner_id=importer.id,
                name="Shared dataset",
                source_filename="shared.xlsx",
                sheet_name="Shared",
                record_count=1,
                fields=[
                    DatasetField(
                        key="field_1",
                        source_name="Value",
                        display_name="値",
                        position=0,
                    )
                ],
                records=[
                    Record(source_row_number=2, data={"field_1": "Visible record"})
                ],
            )
        )
        session.add(
            Dataset(
                name="Legacy dataset",
                source_filename="legacy.xlsx",
                sheet_name="Legacy",
            )
        )
        session.commit()
    yield engine
    engine.dispose()


async def _get(path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as client:
        return await client.get(path)


def get(path: str) -> httpx.Response:
    return asyncio.run(_get(path))


def test_passwords_are_salted_and_verified() -> None:
    first_hash = hash_password(TEST_PASSWORD)
    second_hash = hash_password(TEST_PASSWORD)

    assert first_hash != second_hash
    assert TEST_PASSWORD not in first_hash
    assert verify_password(TEST_PASSWORD, first_hash)
    assert not verify_password("incorrect-password", first_hash)
    assert not verify_password(TEST_PASSWORD, "invalid-hash")
    with pytest.raises(ValueError, match="12 to 1024"):
        hash_password("short")


def test_cli_creates_an_account_with_a_hashed_password(
    auth_database: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    passwords = iter([TEST_PASSWORD, TEST_PASSWORD])
    monkeypatch.setattr("app.cli.get_engine", lambda: auth_database)

    def enter_password(prompt: str) -> str:
        del prompt
        return next(passwords)

    monkeypatch.setattr("app.cli.getpass.getpass", enter_password)

    assert create_user("new-viewer@example.test", "viewer") == 0
    with Session(auth_database) as session:
        user = session.scalar(
            select(User).where(User.email == "new-viewer@example.test")
        )
        assert user is not None
        assert user.role == "viewer"
        assert verify_password(TEST_PASSWORD, user.password_hash)
        assert TEST_PASSWORD not in user.password_hash


def test_unauthenticated_users_are_redirected_from_protected_pages(
    auth_database: Engine,
) -> None:
    protected_paths = [
        "/",
        "/search?dataset_id=12",
        "/datasets/1/records/1",
        "/import",
    ]
    with (
        patch("app.security.get_engine", return_value=auth_database),
        patch("app.routers.pages.get_engine", return_value=auth_database),
    ):
        for path in protected_paths:
            response = get(path)
            assert response.status_code == 303
            assert response.headers["location"].startswith("/login?next=")


def test_successful_login_sets_secure_session_cookie_and_redirects(
    auth_database: Engine,
) -> None:
    async def run() -> tuple[httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=False,
        ) as client:
            response = await sign_in(client, next_url="/search?dataset_id=5")
            page = await client.get("/search")
            return response, page

    with (
        patch("app.security.get_engine", return_value=auth_database),
        patch("app.routers.auth.get_engine", return_value=auth_database),
        patch("app.routers.pages.get_engine", return_value=auth_database),
    ):
        response, page = asyncio.run(run())

    assert response.status_code == 303
    assert response.headers["location"] == "/search?dataset_id=5"
    cookie = response.headers["set-cookie"]
    assert "akashic_session=" in cookie
    assert "httponly" in cookie.lower()
    assert "samesite=lax" in cookie.lower()
    assert page.status_code == 200
    assert TEST_EMAIL in page.text
    with Session(auth_database) as session:
        user = session.query(User).filter_by(email=TEST_EMAIL).one()
        assert user.password_hash.startswith("pbkdf2_sha256$")
        assert user.password_hash != TEST_PASSWORD


def test_login_errors_are_generic_and_open_redirects_are_rejected(
    auth_database: Engine,
) -> None:
    async def run() -> tuple[httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=False,
        ) as client:
            invalid = await sign_in(
                client,
                email="nobody@example.test",
                password="wrong-password-value",
            )
            valid = await sign_in(
                client,
                next_url="//attacker.example",
            )
            return invalid, valid

    with (
        patch("app.security.get_engine", return_value=auth_database),
        patch("app.routers.auth.get_engine", return_value=auth_database),
    ):
        invalid, valid = asyncio.run(run())

    assert invalid.status_code == 401
    assert "メールアドレスまたはパスワードを確認してください" in invalid.text
    assert valid.status_code == 303
    assert valid.headers["location"] == "/"


def test_inactive_accounts_cannot_sign_in(auth_database: Engine) -> None:
    async def run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=False,
        ) as client:
            return await sign_in(
                client,
                email="inactive@example.test",
            )

    with patch("app.routers.auth.get_engine", return_value=auth_database):
        response = asyncio.run(run())

    assert response.status_code == 401
    assert "アカウントが無効" not in response.text


def test_accounts_cannot_read_each_others_datasets_or_import_data(
    auth_database: Engine,
) -> None:
    async def run(dataset_id: int) -> tuple[httpx.Response, ...]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=False,
        ) as client:
            login_page = await client.get("/login")
            match = re.search(r'name="csrf_token" value="([^"]+)"', login_page.text)
            assert match is not None
            await sign_in(client, email="viewer@example.test")
            datasets_page = await client.get("/")
            search_page = await client.get(
                "/search",
                params={"dataset_id": dataset_id},
            )
            detail_page = await client.get(
                f"/datasets/{dataset_id}/records/1",
            )
            import_page = await client.get("/import")
            upload_response = await client.post(
                "/import/preview",
                data={"csrf_token": match.group(1)},
            )
            return (
                datasets_page,
                search_page,
                detail_page,
                import_page,
                upload_response,
            )

    with (
        patch("app.security.get_engine", return_value=auth_database),
        patch("app.routers.auth.get_engine", return_value=auth_database),
        patch("app.routers.pages.get_engine", return_value=auth_database),
    ):
        with Session(auth_database) as session:
            dataset = session.scalar(select(Dataset))
            assert dataset is not None
            dataset_id = dataset.id
        (
            datasets_page,
            search_page,
            detail_page,
            import_page,
            upload_response,
        ) = asyncio.run(run(dataset_id))

    assert datasets_page.status_code == 200
    assert "Shared dataset" not in datasets_page.text
    assert search_page.status_code == 400
    assert "Visible record" not in search_page.text
    assert detail_page.status_code == 404
    assert import_page.status_code == 403
    assert "この操作はできません" in import_page.text
    assert upload_response.status_code == 403
    assert "この操作はできません" in upload_response.text


def test_registration_creates_an_importer_and_starts_a_session(
    auth_database: Engine,
) -> None:
    async def run() -> tuple[httpx.Response, httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=False,
        ) as client:
            page = await client.get("/register")
            token_match = re.search(
                r'name="csrf_token" value="([^"]+)"',
                page.text,
            )
            assert token_match is not None
            response = await client.post(
                "/register",
                data={
                    "email": "NEW-USER@example.test",
                    "password": "New-account-password-123",
                    "password_confirmation": "New-account-password-123",
                    "csrf_token": token_match.group(1),
                },
            )
            import_page = await client.get("/import")
            datasets_page = await client.get("/")
            return response, import_page, datasets_page

    with (
        patch("app.routers.auth.get_engine", return_value=auth_database),
        patch("app.security.get_engine", return_value=auth_database),
        patch("app.routers.pages.get_engine", return_value=auth_database),
    ):
        response, import_page, datasets_page = asyncio.run(run())

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert import_page.status_code == 200
    assert datasets_page.status_code == 200
    assert "Shared dataset" not in datasets_page.text
    assert "Legacy dataset" not in datasets_page.text
    with Session(auth_database) as session:
        user = session.scalar(
            select(User).where(User.email == "new-user@example.test")
        )
        assert user is not None
        assert user.role == "importer"
        assert user.password_hash.startswith("pbkdf2_sha256$")
        assert user.password_hash != "New-account-password-123"


def test_registration_rejects_duplicate_email_and_short_password(
    auth_database: Engine,
) -> None:
    async def run() -> tuple[httpx.Response, httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=False,
        ) as client:
            page = await client.get("/register")
            token_match = re.search(
                r'name="csrf_token" value="([^"]+)"',
                page.text,
            )
            assert token_match is not None
            csrf_token = token_match.group(1)
            missing_csrf = await client.post(
                "/register",
                data={
                    "email": "no-csrf@example.test",
                    "password": TEST_PASSWORD,
                    "password_confirmation": TEST_PASSWORD,
                },
            )
            duplicate = await client.post(
                "/register",
                data={
                    "email": TEST_EMAIL.upper(),
                    "password": TEST_PASSWORD,
                    "password_confirmation": TEST_PASSWORD,
                    "csrf_token": csrf_token,
                },
            )
            short_password = await client.post(
                "/register",
                data={
                    "email": "short-password@example.test",
                    "password": "short",
                    "password_confirmation": "short",
                    "csrf_token": csrf_token,
                },
            )
            return missing_csrf, duplicate, short_password

    with patch("app.routers.auth.get_engine", return_value=auth_database):
        missing_csrf, duplicate, short_password = asyncio.run(run())

    assert missing_csrf.status_code == 403
    assert duplicate.status_code == 409
    assert "既に登録されています" in duplicate.text
    assert short_password.status_code == 400
    assert "12〜1024文字" in short_password.text


def test_registration_database_error_links_to_safe_diagnostics(
    auth_database: Engine,
) -> None:
    async def run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=False,
        ) as client:
            page = await client.get("/register")
            token_match = re.search(
                r'name="csrf_token" value="([^"]+)"',
                page.text,
            )
            assert token_match is not None
            return await client.post(
                "/register",
                data={
                    "email": "database-error@example.test",
                    "password": TEST_PASSWORD,
                    "password_confirmation": TEST_PASSWORD,
                    "csrf_token": token_match.group(1),
                },
            )

    database_session = MagicMock()
    database_session.__enter__.return_value.commit.side_effect = SQLAlchemyError(
        "private database detail"
    )
    with (
        patch("app.routers.auth.get_engine", return_value=auth_database),
        patch("app.routers.auth.Session", return_value=database_session),
    ):
        response = asyncio.run(run())

    assert response.status_code == 503
    assert "DB状態診断をご確認ください" in response.text
    assert "/health/ready" in response.text
    assert "DATABASE_UNREACHABLE" in response.text
    assert "Internal Database URL" in response.text
    assert "private database detail" not in response.text


def test_missing_database_configuration_does_not_cause_internal_server_error() -> None:
    async def run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=False,
        ) as client:
            page = await client.get("/register")
            token_match = re.search(
                r'name="csrf_token" value="([^"]+)"',
                page.text,
            )
            assert token_match is not None
            return await client.post(
                "/register",
                data={
                    "email": "configuration-error@example.test",
                    "password": TEST_PASSWORD,
                    "password_confirmation": TEST_PASSWORD,
                    "csrf_token": token_match.group(1),
                },
            )

    with patch(
        "app.routers.auth.get_engine",
        side_effect=DatabaseConfigurationError("DATABASE_URL_MISSING"),
    ):
        response = asyncio.run(run())

    assert response.status_code == 503
    assert "DB状態診断をご確認ください" in response.text
    assert "Internal Server Error" not in response.text


def test_importer_can_open_import_page_but_csrf_is_required(
    auth_database: Engine,
) -> None:
    async def run() -> tuple[httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=False,
        ) as client:
            await sign_in(client)
            page = await client.get("/import")
            response = await client.post("/import/preview", data={})
            return page, response

    with (
        patch("app.security.get_engine", return_value=auth_database),
        patch("app.routers.auth.get_engine", return_value=auth_database),
        patch("app.routers.pages.get_engine", return_value=auth_database),
    ):
        page, response = asyncio.run(run())

    assert page.status_code == 200
    assert "csrf_token" in page.text
    assert response.status_code == 403


def test_logout_clears_the_session(auth_database: Engine) -> None:
    async def run() -> tuple[httpx.Response, httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=False,
        ) as client:
            await sign_in(client)
            page = await client.get("/")
            match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
            assert match is not None
            logout_response = await client.post(
                "/logout",
                data={"csrf_token": match.group(1)},
            )
            protected_response = await client.get("/search")
            return page, logout_response, protected_response

    with (
        patch("app.security.get_engine", return_value=auth_database),
        patch("app.routers.auth.get_engine", return_value=auth_database),
        patch("app.routers.pages.get_engine", return_value=auth_database),
    ):
        page, logout_response, protected_response = asyncio.run(run())

    assert page.status_code == 200
    assert logout_response.status_code == 303
    assert logout_response.headers["location"] == "/login"
    assert protected_response.status_code == 303


def test_login_rejects_a_modified_session_cookie(auth_database: Engine) -> None:
    async def run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=False,
        ) as client:
            login_response = await sign_in(client)
            session_cookie = login_response.cookies.get("akashic_session")
            assert session_cookie
            client.cookies.set("akashic_session", session_cookie + "tampered")
            return await client.get("/")

    with (
        patch("app.security.get_engine", return_value=auth_database),
        patch("app.routers.auth.get_engine", return_value=auth_database),
    ):
        response = asyncio.run(run())

    assert response.status_code == 303
