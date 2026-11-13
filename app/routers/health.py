import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from app import models
from app.database import DatabaseConfigurationError, get_engine

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)

DIAGNOSTIC_ACTIONS = {
    "DATABASE_URL_MISSING": (
        "RenderのWeb Service > EnvironmentでDATABASE_URLを設定してください。"
        "Render PostgreSQLのInternal Database URLを同じリージョンのWeb Serviceに接続します。"
    ),
    "DATABASE_URL_INVALID": (
        "DATABASE_URLの形式と接続先を確認してください。"
        "本番ではPostgreSQLのInternal Database URLを使用してください。"
    ),
    "DATABASE_AUTH_FAILED": (
        "DATABASE_URLが現在のPostgreSQLの接続情報と一致するか確認し、"
        "Web Serviceの環境変数を最新の接続文字列に更新してください。"
    ),
    "DATABASE_NOT_FOUND": (
        "DATABASE_URLが存在するPostgreSQLデータベースを指しているか確認してください。"
    ),
    "DATABASE_HOST_NOT_FOUND": (
        "DATABASE_URLのホスト名を確認し、Render PostgreSQLのInternal Database URLを使用してください。"
    ),
    "DATABASE_CONNECTION_REFUSED": (
        "PostgreSQLサービスが稼働中か確認し、DATABASE_URLが正しいDBを指しているか確認してください。"
    ),
    "DATABASE_TIMEOUT": (
        "PostgreSQLサービスの稼働状態とリージョンを確認してください。"
        "Web ServiceとDBは同じリージョンに配置してください。"
    ),
    "DATABASE_TLS_ERROR": (
        "DBの接続方式とTLS設定を確認し、Renderが提供する接続URLを使用してください。"
    ),
    "DATABASE_PERMISSION_DENIED": (
        "DATABASE_URLのDBユーザーに接続とアプリ用テーブルへの権限があるか確認してください。"
    ),
    "SCHEMA_OUTDATED": (
        "デプロイログでAlembicマイグレーションの成功を確認し、"
        "失敗している場合は原因を修正して再デプロイしてください。"
    ),
    "SCHEMA_CHECK_FAILED": (
        "DB接続はできていますがスキーマ確認に失敗しました。"
        "マイグレーション状態とDBユーザー権限を確認してください。"
    ),
    "DATABASE_UNREACHABLE": (
        "DBサービスの稼働状態とDATABASE_URLを確認してください。"
        "接続文字列自体は公開しないでください。"
    ),
}


@router.get("/health/live", name="live")
def live() -> dict[str, str]:
    return {"status": "ok"}


def classify_database_error(error: SQLAlchemyError) -> str:
    original_error = getattr(error, "orig", error)
    sqlstate = getattr(original_error, "sqlstate", None) or getattr(
        original_error,
        "pgcode",
        None,
    )
    if sqlstate in {"28P01", "28000"}:
        return "DATABASE_AUTH_FAILED"
    if sqlstate == "3D000":
        return "DATABASE_NOT_FOUND"
    if sqlstate == "42501":
        return "DATABASE_PERMISSION_DENIED"
    if sqlstate in {"08001", "08006", "57P03"}:
        return "DATABASE_UNREACHABLE"

    message = str(original_error).casefold()
    if (
        "password authentication failed" in message
        or "authentication failed" in message
        or ("role" in message and "does not exist" in message)
    ):
        return "DATABASE_AUTH_FAILED"
    if "database" in message and (
        "does not exist" in message or "unknown database" in message
    ):
        return "DATABASE_NOT_FOUND"
    if (
        "permission denied" in message
        or "not authorized" in message
        or "no pg_hba.conf entry" in message
    ):
        return "DATABASE_PERMISSION_DENIED"
    if any(
        phrase in message
        for phrase in (
            "could not translate host name",
            "getaddrinfo failed",
            "name or service not known",
            "nodename nor servname",
            "temporary failure in name resolution",
        )
    ):
        return "DATABASE_HOST_NOT_FOUND"
    if "connection refused" in message:
        return "DATABASE_CONNECTION_REFUSED"
    if "timeout" in message or "timed out" in message:
        return "DATABASE_TIMEOUT"
    if "ssl" in message or "certificate" in message:
        return "DATABASE_TLS_ERROR"
    return "DATABASE_UNREACHABLE"


def database_unavailable(diagnostic: str) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "status": "unavailable",
            "database": "disconnected",
            "diagnostic": diagnostic,
            "action": DIAGNOSTIC_ACTIONS.get(
                diagnostic,
                DIAGNOSTIC_ACTIONS["DATABASE_UNREACHABLE"],
            ),
        },
    )


@router.get("/health/ready", name="ready", response_model=None)
def ready() -> JSONResponse | dict[str, str]:
    try:
        engine = get_engine()
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except DatabaseConfigurationError as error:
        logger.error("Database configuration error: %s", error.diagnostic)
        return database_unavailable(error.diagnostic)
    except SQLAlchemyError as error:
        logger.exception("Database readiness check failed")
        return database_unavailable(classify_database_error(error))

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
                "action": DIAGNOSTIC_ACTIONS["SCHEMA_CHECK_FAILED"],
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
                "action": DIAGNOSTIC_ACTIONS["SCHEMA_OUTDATED"],
            },
        )

    return {"status": "ok", "database": "connected"}
