import logging
import re
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_engine
from app.models import User
from app.security import (
    CSRF_SESSION_KEY,
    SESSION_USER_KEY,
    dummy_password_hash,
    get_csrf_token,
    hash_password,
    safe_redirect_target,
    verify_csrf_token,
    verify_password,
)
from app.web import templates

router = APIRouter()
logger = logging.getLogger(__name__)
DUMMY_PASSWORD_HASH = dummy_password_hash()
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def render_login(
    request: Request,
    *,
    status_code: int = 200,
    error_message: str | None = None,
    next_url: str = "/",
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="pages/login.html",
        context={
            "app_name": get_settings().app_name,
            "csrf_token": get_csrf_token(request),
            "error_message": error_message,
            "next_url": safe_redirect_target(next_url),
            "current_user": getattr(request.state, "current_user", None),
        },
        status_code=status_code,
    )


def render_registration(
    request: Request,
    *,
    status_code: int = 200,
    error_message: str | None = None,
    email: str = "",
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="pages/register.html",
        context={
            "app_name": get_settings().app_name,
            "csrf_token": get_csrf_token(request),
            "error_message": error_message,
            "email": email,
            "current_user": getattr(request.state, "current_user", None),
        },
        status_code=status_code,
    )


@router.get("/login", response_class=HTMLResponse, name="login")
def login_page(
    request: Request,
    next_url: str = Query(default="/", alias="next"),
) -> HTMLResponse:
    return render_login(request, next_url=next_url)


@router.get("/register", response_class=HTMLResponse, name="register")
def register_page(request: Request) -> HTMLResponse:
    return render_registration(request)


@router.post(
    "/register",
    response_class=HTMLResponse,
    response_model=None,
    name="register_submit",
)
def register_submit(
    request: Request,
    email: Annotated[str, Form(max_length=320)],
    password: Annotated[str, Form(max_length=1024)],
    password_confirmation: Annotated[str, Form(max_length=1024)],
    _csrf_valid: Annotated[None, Depends(verify_csrf_token)],
) -> HTMLResponse | RedirectResponse:
    del _csrf_valid
    normalized_email = email.strip().casefold()
    if len(normalized_email) > 320 or not EMAIL_PATTERN.fullmatch(normalized_email):
        return render_registration(
            request,
            status_code=400,
            error_message="有効なメールアドレスを入力してください。",
            email=normalized_email,
        )
    if password != password_confirmation:
        return render_registration(
            request,
            status_code=400,
            error_message="パスワードが一致しません。",
            email=normalized_email,
        )
    try:
        password_hash = hash_password(password)
    except ValueError:
        return render_registration(
            request,
            status_code=400,
            error_message="パスワードは12〜1024文字で入力してください。",
            email=normalized_email,
        )

    try:
        with Session(get_engine()) as session:
            user = User(
                email=normalized_email,
                password_hash=password_hash,
                role="importer",
            )
            session.add(user)
            session.commit()
            user_id = user.id
    except IntegrityError:
        return render_registration(
            request,
            status_code=409,
            error_message="このメールアドレスは既に登録されています。",
            email=normalized_email,
        )
    except SQLAlchemyError:
        logger.exception("Could not register a new account")
        return render_registration(
            request,
            status_code=503,
            error_message="アカウントを作成できません。時間をおいて再度お試しください。",
            email=normalized_email,
        )

    request.session.clear()
    request.session[SESSION_USER_KEY] = user_id
    request.session[CSRF_SESSION_KEY] = secrets.token_urlsafe(32)
    return RedirectResponse("/", status_code=303)


@router.post(
    "/login",
    response_class=HTMLResponse,
    response_model=None,
    name="login_submit",
)
def login_submit(
    request: Request,
    email: Annotated[str, Form(max_length=320)],
    password: Annotated[str, Form(max_length=1024)],
    _csrf_valid: Annotated[None, Depends(verify_csrf_token)],
    next_url: Annotated[str, Form()] = "/",
) -> HTMLResponse | RedirectResponse:
    del _csrf_valid
    try:
        normalized_email = email.strip().casefold()
        with Session(get_engine()) as session:
            user = session.scalar(
                select(User).where(
                    User.email == normalized_email,
                    User.is_active.is_(True),
                )
            )
            password_hash = user.password_hash if user is not None else DUMMY_PASSWORD_HASH
            password_is_valid = verify_password(password, password_hash)
    except SQLAlchemyError:
        logger.exception("Could not authenticate a login request")
        return render_login(
            request,
            status_code=503,
            error_message="ログインを確認できません。時間をおいて再度お試しください。",
            next_url=next_url,
        )

    if user is None or not password_is_valid:
        return render_login(
            request,
            status_code=401,
            error_message="メールアドレスまたはパスワードを確認してください。",
            next_url=next_url,
        )

    request.session.clear()
    request.session[SESSION_USER_KEY] = user.id
    request.session[CSRF_SESSION_KEY] = secrets.token_urlsafe(32)
    return RedirectResponse(
        safe_redirect_target(next_url),
        status_code=303,
    )


@router.post("/logout", name="logout")
def logout(
    request: Request,
    _csrf_valid: Annotated[None, Depends(verify_csrf_token)],
) -> RedirectResponse:
    del _csrf_valid
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
