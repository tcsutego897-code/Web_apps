import asyncio
from unittest.mock import Mock, patch

import httpx
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

from app.database import Base
from app.main import app


async def _get(path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        return await client.get(path)


def get(path: str) -> httpx.Response:
    return asyncio.run(_get(path))


def test_page_shells_render(tmp_path) -> None:
    pages = {
        "/": "データセット",
        "/search": "検索・閲覧",
        "/import": "Excel取り込み",
        "/login": "ログイン",
    }

    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'pages.db'}")
    Base.metadata.create_all(engine)
    try:
        with patch("app.routers.pages.get_engine", return_value=engine):
            for path, heading in pages.items():
                response = get(path)
                assert response.status_code == 200
                assert heading in response.text
                assert 'aria-label="メインメニュー"' in response.text
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


def test_readiness_check_confirms_database_connection() -> None:
    test_engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with patch("app.routers.health.get_engine", return_value=test_engine):
            response = get("/health/ready")
    finally:
        test_engine.dispose()

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "connected"}


def test_readiness_check_reports_database_failure() -> None:
    unavailable_engine = Mock()
    unavailable_engine.connect.side_effect = SQLAlchemyError("database offline")
    with patch("app.routers.health.get_engine", return_value=unavailable_engine):
        response = get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "database": "disconnected"}
