import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.database import get_engine

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)


@router.get("/health/live", name="live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", name="ready", response_model=None)
def ready() -> JSONResponse | dict[str, str]:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.exception("Database readiness check failed")
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "database": "disconnected"},
        )

    return {"status": "ok", "database": "connected"}
