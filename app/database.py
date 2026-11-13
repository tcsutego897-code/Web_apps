from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class DatabaseConfigurationError(SQLAlchemyError):
    def __init__(self, diagnostic: str) -> None:
        super().__init__(diagnostic)
        self.diagnostic = diagnostic


class Base(DeclarativeBase):
    """Base class for database models added in later phases."""


def normalize_database_url(database_url: str) -> str:
    url = make_url(database_url)
    if url.drivername in {"postgres", "postgresql"}:
        url = url.set(drivername="postgresql+psycopg")
    return url.render_as_string(hide_password=False)


def get_database_url() -> str:
    settings = get_settings()
    database_url = settings.database_url.strip()
    if not database_url:
        raise DatabaseConfigurationError("DATABASE_URL_MISSING")

    try:
        normalized_url = normalize_database_url(database_url)
        backend_name = make_url(normalized_url).get_backend_name()
    except ArgumentError as error:
        raise DatabaseConfigurationError("DATABASE_URL_INVALID") from error

    if backend_name not in {"postgresql", "sqlite"}:
        raise DatabaseConfigurationError("DATABASE_URL_INVALID")
    if (
        settings.environment.casefold() in {"production", "prod"}
        and backend_name != "postgresql"
    ):
        raise DatabaseConfigurationError("DATABASE_URL_INVALID")
    return normalized_url


@lru_cache
def get_engine() -> Engine:
    database_url = get_database_url()
    is_postgresql = make_url(database_url).get_backend_name() == "postgresql"
    connect_args = {"connect_timeout": 5} if is_postgresql else {}

    return create_engine(
        database_url,
        connect_args=connect_args,
        pool_pre_ping=True,
    )
