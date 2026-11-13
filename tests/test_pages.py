import asyncio
from unittest.mock import Mock, patch

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import Base, DatabaseConfigurationError, get_database_url
from app.main import app
from tests.security_helpers import add_user, sign_in


async def _get(path: str, *, authenticate: bool = False) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as client:
        if authenticate:
            await sign_in(client)
        return await client.get(path)


def get(path: str, *, authenticate: bool = False) -> httpx.Response:
    return asyncio.run(_get(path, authenticate=authenticate))


def test_page_shells_render(tmp_path) -> None:
    pages = {
        "/": "データセット",
        "/search": "検索・閲覧",
        "/import": "Excel取り込み",
        "/login": "ログイン",
        "/register": "新規登録",
    }

    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'pages.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        add_user(session)
    try:
        with (
            patch("app.routers.auth.get_engine", return_value=engine),
            patch("app.security.get_engine", return_value=engine),
            patch("app.routers.pages.get_engine", return_value=engine),
        ):
            for path, heading in pages.items():
                response = get(path, authenticate=True)
                assert response.status_code == 200
                assert heading in response.text
                assert 'aria-label="メインメニュー"' in response.text
                if path == "/import":
                    assert 'data-loading-message="Excelファイルを確認しています..."' in response.text
                    assert 'role="status" aria-live="polite" hidden' in response.text
                    assert "form.setAttribute(\"aria-busy\", \"true\")" in response.text
                if path == "/login":
                    assert 'name="csrf_token"' in response.text
    finally:
        engine.dispose()


def test_unknown_page_shows_japanese_not_found_page() -> None:
    response = get("/does-not-exist")

    assert response.status_code == 404
    assert "ページが見つかりません" in response.text


def test_liveness_check_does_not_require_database() -> None:
    response = get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_check_confirms_database_connection(tmp_path) -> None:
    test_engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'ready.db'}")
    Base.metadata.create_all(test_engine)
    try:
        with patch("app.routers.health.get_engine", return_value=test_engine):
            response = get("/health/ready")
    finally:
        test_engine.dispose()

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "connected"}


def test_readiness_check_reports_outdated_database_schema() -> None:
    test_engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with patch("app.routers.health.get_engine", return_value=test_engine):
            response = get("/health/ready")
    finally:
        test_engine.dispose()

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "database": "connected",
        "schema": "outdated",
        "diagnostic": "SCHEMA_OUTDATED",
        "action": (
            "デプロイログでAlembicマイグレーションの成功を確認し、"
            "失敗している場合は原因を修正して再デプロイしてください。"
        ),
    }


def test_readiness_check_reports_database_failure() -> None:
    unavailable_engine = Mock()
    unavailable_engine.connect.side_effect = SQLAlchemyError("database offline")
    with patch("app.routers.health.get_engine", return_value=unavailable_engine):
        response = get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "database": "disconnected",
        "diagnostic": "DATABASE_UNREACHABLE",
        "action": (
            "DBサービスの稼働状態とDATABASE_URLを確認してください。"
            "接続文字列自体は公開しないでください。"
        ),
    }


def test_missing_production_database_url_is_not_replaced_with_localhost(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.database.get_settings",
        lambda: Settings(environment="production", database_url=""),
    )

    with pytest.raises(DatabaseConfigurationError) as error:
        get_database_url()

    assert error.value.diagnostic == "DATABASE_URL_MISSING"


def test_production_database_url_must_use_postgresql(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.database.get_settings",
        lambda: Settings(
            environment="production",
            database_url="sqlite+pysqlite:///./local.db",
        ),
    )

    with pytest.raises(DatabaseConfigurationError) as error:
        get_database_url()

    assert error.value.diagnostic == "DATABASE_URL_INVALID"


@pytest.mark.parametrize(
    ("message", "diagnostic"),
    [
        ("password authentication failed for user", "DATABASE_AUTH_FAILED"),
        ("database does not exist", "DATABASE_NOT_FOUND"),
        ("could not translate host name", "DATABASE_HOST_NOT_FOUND"),
        ("connection refused", "DATABASE_CONNECTION_REFUSED"),
        ("connection timed out", "DATABASE_TIMEOUT"),
        ("SSL certificate verify failed", "DATABASE_TLS_ERROR"),
    ],
)
def test_readiness_classifies_postgresql_connection_failures(
    message: str,
    diagnostic: str,
) -> None:
    unavailable_engine = Mock()
    unavailable_engine.connect.side_effect = OperationalError(
        "SELECT 1",
        {},
        OSError(message),
    )
    with patch("app.routers.health.get_engine", return_value=unavailable_engine):
        response = get("/health/ready")

    assert response.status_code == 503
    assert response.json()["diagnostic"] == diagnostic
    assert response.json()["action"]
    assert "password" not in response.text.casefold()


def test_readiness_reports_missing_database_url_action() -> None:
    with patch(
        "app.routers.health.get_engine",
        side_effect=DatabaseConfigurationError("DATABASE_URL_MISSING"),
    ):
        response = get("/health/ready")

    assert response.status_code == 503
    assert response.json()["diagnostic"] == "DATABASE_URL_MISSING"
    assert "DATABASE_URL" in response.json()["action"]
