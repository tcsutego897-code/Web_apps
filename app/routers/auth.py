import logging
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_engine
from app.models import User
from app.security import (
    CSRF_SESSION_KEY,
    SESSION_USER_KEY,
    dummy_password_hash,
    get_csrf_token,
    safe_redirect_target,
    verify_csrf_token,
    verify_password,
)
from app.web import templates

router = APIRouter()
logger = logging.getLogger(__name__)
DUMMY_PASSWORD_HASH = dummy_password_hash()


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


@router.get("/login", response_class=HTMLResponse, name="login")
def login_page(
    request: Request,
    next_url: str = Query(default="/", alias="next"),
) -> HTMLResponse:
    return render_login(request, next_url=next_url)


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
