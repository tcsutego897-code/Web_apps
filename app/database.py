from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    """Base class for database models added in later phases."""


@lru_cache
def get_engine() -> Engine:
    database_url = get_settings().database_url
    connect_args = {"connect_timeout": 5} if database_url.startswith("postgresql") else {}

    return create_engine(
        database_url,
        connect_args=connect_args,
        pool_pre_ping=True,
    )
