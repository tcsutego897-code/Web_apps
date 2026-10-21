import logging
import ntpath
import re
import tempfile
import time
from pathlib import Path
from secrets import token_urlsafe

from fastapi import UploadFile

from app.config import get_settings

logger = logging.getLogger(__name__)
STAGING_DIRECTORY = Path(tempfile.gettempdir()) / "akashic-record-imports"
STAGING_MAX_AGE_SECONDS = 30 * 60
STAGING_MAX_FILES = 10
STAGING_MAX_TOTAL_BYTES = 50 * 1024 * 1024
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")


class UploadError(ValueError):
    """A safe, user-facing upload validation error."""


def normalize_filename(filename: str | None) -> str:
    name = ntpath.basename((filename or "").replace("/", "\\")).strip()
    if not name:
        raise UploadError("アップロードするファイルを選択してください。")
    if len(name) > 255:
        raise UploadError("ファイル名は255文字以内にしてください。")
    if Path(name).suffix.casefold() != ".xlsx":
        raise UploadError("取り込めるのは .xlsx 形式のファイルだけです。")
    return name


async def read_upload(upload: UploadFile) -> tuple[str, bytes]:
    filename = normalize_filename(upload.filename)
    maximum = get_settings().max_upload_size_bytes
    content = bytearray()

    while chunk := await upload.read(64 * 1024):
        content.extend(chunk)
        if len(content) > maximum:
            raise UploadError(
                f"ファイルの上限は {maximum:,} bytes "
                f"({maximum / (1024 * 1024):.1f} MiB) です。"
            )

    if not content:
        raise UploadError("ファイルが空です。")
    return filename, bytes(content)


def _remove_expired_uploads() -> None:
    if not STAGING_DIRECTORY.exists():
        return

    expiration_time = time.time() - STAGING_MAX_AGE_SECONDS
    for path in STAGING_DIRECTORY.glob("*.xlsx"):
        try:
            if path.stat().st_mtime < expiration_time:
                path.unlink()
        except FileNotFoundError:
            continue
        except OSError:
            logger.exception("Could not remove expired staged upload: %s", path.name)


def stage_upload(content: bytes) -> str:
    _remove_expired_uploads()
    try:
        STAGING_DIRECTORY.mkdir(parents=True, exist_ok=True)
        staged_files = list(STAGING_DIRECTORY.glob("*.xlsx"))
        staged_bytes = sum(
            path.stat().st_size for path in staged_files if path.is_file()
        )
    except OSError as error:
        logger.exception("Could not prepare the staged upload directory")
        raise UploadError("一時保管先を準備できませんでした。") from error
    if len(staged_files) >= STAGING_MAX_FILES or (
        staged_bytes + len(content) > STAGING_MAX_TOTAL_BYTES
    ):
        raise UploadError(
            "一時保管中のファイルが上限に達しています。時間をおいて再度お試しください。"
        )

    for _ in range(3):
        token = token_urlsafe(32)
        path = STAGING_DIRECTORY / f"{token}.xlsx"
        try:
            with path.open("xb") as staged_file:
                staged_file.write(content)
            return token
        except FileExistsError:
            continue
        except OSError as error:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.exception("Could not remove an incomplete staged upload")
            logger.exception("Could not stage an uploaded workbook")
            raise UploadError("アップロードファイルを一時保管できませんでした。") from error

    raise UploadError("一時保管先を確保できませんでした。もう一度お試しください。")


def read_staged_upload(token: str) -> bytes:
    if not TOKEN_PATTERN.fullmatch(token):
        raise UploadError("プレビューの有効期限が切れました。ファイルを選び直してください。")

    path = STAGING_DIRECTORY / f"{token}.xlsx"
    try:
        if time.time() - path.stat().st_mtime > STAGING_MAX_AGE_SECONDS:
            path.unlink(missing_ok=True)
            raise UploadError(
                "プレビューの有効期限が切れました。ファイルを選び直してください。"
            )
        maximum = get_settings().max_upload_size_bytes
        content = path.read_bytes()
    except FileNotFoundError as error:
        raise UploadError(
            "プレビュー用ファイルがありません。ファイルを選び直してください。"
        ) from error
    except OSError as error:
        logger.exception("Could not read a staged workbook")
        raise UploadError("一時保管したファイルを読み込めませんでした。") from error

    if len(content) > maximum:
        delete_staged_upload(token)
        raise UploadError("一時保管したファイルが上限サイズを超えています。")
    return content


def delete_staged_upload(token: str) -> None:
    if not TOKEN_PATTERN.fullmatch(token):
        return
    try:
        (STAGING_DIRECTORY / f"{token}.xlsx").unlink(missing_ok=True)
    except OSError:
        logger.exception("Could not remove a staged workbook")
