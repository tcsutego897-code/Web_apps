from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    """Base class for database models added in later phases."""


def normalize_database_url(database_url: str) -> str:
    url = make_url(database_url)
    if url.drivername in {"postgres", "postgresql"}:
        url = url.set(drivername="postgresql+psycopg")
    return url.render_as_string(hide_password=False)


@lru_cache
def get_engine() -> Engine:
    database_url = normalize_database_url(get_settings().database_url)
    is_postgresql = make_url(database_url).get_backend_name() == "postgresql"
    connect_args = {"connect_timeout": 5} if is_postgresql else {}

    return create_engine(
        database_url,
        connect_args=connect_args,
        pool_pre_ping=True,
    )
