import base64
import binascii
import hashlib
import hmac
import logging
import secrets
from typing import Annotated
from urllib.parse import quote

from fastapi import Depends, Form, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_engine
from app.models import User

logger = logging.getLogger(__name__)
PASSWORD_HASH_SCHEME = "pbkdf2_sha256"
PASSWORD_HASH_ITERATIONS = 600_000
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 1024
SESSION_USER_KEY = "user_id"
CSRF_SESSION_KEY = "csrf_token"


def _encode_password(password: str, salt: bytes) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_HASH_ITERATIONS,
        dklen=32,
    )
    encoded_salt = base64.urlsafe_b64encode(salt).decode("ascii").rstrip("=")
    encoded_digest = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return (
        f"{PASSWORD_HASH_SCHEME}${PASSWORD_HASH_ITERATIONS}"
        f"${encoded_salt}${encoded_digest}"
    )


def hash_password(password: str) -> str:
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        raise ValueError(
            f"Password must be {PASSWORD_MIN_LENGTH} to {PASSWORD_MAX_LENGTH} characters."
        )
    return _encode_password(password, secrets.token_bytes(16))


def verify_password(password: str, password_hash: str) -> bool:
    if len(password) > PASSWORD_MAX_LENGTH:
        return False

    try:
        scheme, iterations_text, salt_text, digest_text = password_hash.split("$")
        iterations = int(iterations_text)
        if (
            scheme != PASSWORD_HASH_SCHEME
            or not 100_000 <= iterations <= PASSWORD_HASH_ITERATIONS
        ):
            return False
        salt = base64.urlsafe_b64decode(salt_text + "=" * (-len(salt_text) % 4))
        expected_digest = base64.urlsafe_b64decode(
            digest_text + "=" * (-len(digest_text) % 4)
        )
    except (ValueError, binascii.Error):
        return False

    if len(salt) != 16 or len(expected_digest) != 32:
        return False

    actual_digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
        dklen=32,
    )
    return hmac.compare_digest(actual_digest, expected_digest)


def get_csrf_token(request: Request) -> str:
    token = request.session.get(CSRF_SESSION_KEY)
    if not isinstance(token, str):
        token = secrets.token_urlsafe(32)
        request.session[CSRF_SESSION_KEY] = token
    return token


def verify_csrf_token(
    request: Request,
    csrf_token: Annotated[str, Form()] = "",
) -> None:
    validate_csrf_token(request, csrf_token)


def validate_csrf_token(request: Request, csrf_token: str) -> None:
    expected_token = request.session.get(CSRF_SESSION_KEY, "")
    if not isinstance(expected_token, str) or not secrets.compare_digest(
        csrf_token,
        expected_token,
    ):
        raise HTTPException(status_code=403, detail="フォームの有効期限が切れました。")


def get_optional_user(request: Request) -> User | None:
    user_id = request.session.get(SESSION_USER_KEY)
    if type(user_id) is not int:
        return None

    try:
        with Session(get_engine()) as session:
            user = session.scalar(
                select(User).where(User.id == user_id, User.is_active.is_(True))
            )
            if user is None:
                request.session.clear()
                return None
            request.state.current_user = user
            return user
    except SQLAlchemyError as error:
        logger.exception("Could not validate the current user session")
        raise HTTPException(
            status_code=503,
            detail="認証状態を確認できません。しばらくしてから再度お試しください。",
        ) from error


def require_authenticated_user(request: Request) -> User:
    user = get_optional_user(request)
    if user is None:
        target = request.url.path
        if request.url.query:
            target += "?" + request.url.query
        location = "/login?next=" + quote(target, safe="/?=&")
        raise HTTPException(status_code=303, headers={"Location": location})
    return user


def require_importer(
    user: Annotated[User, Depends(require_authenticated_user)],
) -> User:
    if user.role != "importer":
        raise HTTPException(
            status_code=403,
            detail="Excelを取り込む権限がありません。",
        )
    return user


def safe_redirect_target(value: str | None) -> str:
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/"
    if "\\" in value or any(ord(character) < 32 for character in value):
        return "/"
    return value


def dummy_password_hash() -> str:
    return _encode_password(
        "not-a-real-user-password",
        b"dummy-salt-12345",
    )
