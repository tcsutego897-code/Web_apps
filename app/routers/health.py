import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from app import models
from app.database import get_engine

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)


@router.get("/health/live", name="live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", name="ready", response_model=None)
def ready() -> JSONResponse | dict[str, str]:
    try:
        engine = get_engine()
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.exception("Database readiness check failed")
        return JSONResponse(
            status_code=503,
            content={
                "status": "unavailable",
                "database": "disconnected",
                "diagnostic": "DATABASE_UNREACHABLE",
            },
        )

    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            table_names = set(inspector.get_table_names())
            expected_tables = models.Base.metadata.tables
            schema_is_current = all(
                table_name in table_names
                and {
                    column.name
                    for column in expected_table.columns
                }.issubset(
                    {
                        column["name"]
                        for column in inspector.get_columns(table_name)
                    }
                )
                for table_name, expected_table in expected_tables.items()
            )
    except SQLAlchemyError:
        logger.exception("Database schema readiness check failed")
        return JSONResponse(
            status_code=503,
            content={
                "status": "unavailable",
                "database": "connected",
                "schema": "unknown",
                "diagnostic": "SCHEMA_CHECK_FAILED",
            },
        )

    if not schema_is_current:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unavailable",
                "database": "connected",
                "schema": "outdated",
                "diagnostic": "SCHEMA_OUTDATED",
            },
        )

    return {"status": "ok", "database": "connected"}
