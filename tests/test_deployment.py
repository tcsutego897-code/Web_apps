import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.database import normalize_database_url

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "database_url",
    [
        "postgres://db-user:secret@db.example.com:5432/akashic",
        "postgresql://db-user:secret@db.example.com:5432/akashic",
    ],
)
def test_postgresql_urls_use_the_installed_psycopg_driver(
    database_url: str,
) -> None:
    normalized_url = normalize_database_url(database_url)

    assert normalized_url.startswith("postgresql+psycopg://")
    assert "secret@" in normalized_url


def test_sqlite_url_is_not_changed() -> None:
    database_url = "sqlite+pysqlite:///./test.db"

    assert normalize_database_url(database_url) == database_url


def test_migrations_apply_and_database_data_survives_reconnecting(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "deployed-app.db"
    database_url = f"sqlite+pysqlite:///{database_path}"
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    engine = create_engine(database_url)
    with engine.begin() as connection:
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        connection.execute(
            text(
                "INSERT INTO users (email, password_hash, role) "
                "VALUES ('viewer@example.test', 'test-hash', 'viewer')"
            )
        )
    engine.dispose()

    reopened_engine = create_engine(database_url)
    try:
        with reopened_engine.connect() as connection:
            stored_email = connection.scalar(text("SELECT email FROM users"))
    finally:
        reopened_engine.dispose()

    assert revision == "0003_import_row_issues"
    assert stored_email == "viewer@example.test"
