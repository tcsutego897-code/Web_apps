import logging
import re
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_engine
from app.models import Dataset, ImportHistory, User
from app.security import (
    CSRF_SESSION_KEY,
    SESSION_USER_KEY,
    get_csrf_token,
    hash_password,
    require_authenticated_user,
    verify_csrf_token,
    verify_password,
)
from app.web import templates

router = APIRouter()
logger = logging.getLogger(__name__)
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def render_account(
    request: Request,
    *,
    current_user: User,
    status_code: int = 200,
    email_address: str | None = None,
    email_error: str | None = None,
    password_error: str | None = None,
    delete_error: str | None = None,
    email_updated: bool = False,
    password_updated: bool = False,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="pages/account.html",
        context={
            "app_name": get_settings().app_name,
            "csrf_token": get_csrf_token(request),
            "current_user": current_user,
            "email_address": (
                current_user.email if email_address is None else email_address
            ),
            "email_error": email_error,
            "password_error": password_error,
            "delete_error": delete_error,
            "email_updated": email_updated,
            "password_updated": password_updated,
        },
        status_code=status_code,
    )


@router.get("/account", response_class=HTMLResponse, name="account")
def account_page(
    request: Request,
    current_user: User = Depends(require_authenticated_user),
    email_updated: bool = False,
    password_updated: bool = False,
) -> HTMLResponse:
    return render_account(
        request,
        current_user=current_user,
        email_updated=email_updated,
        password_updated=password_updated,
    )


@router.post(
    "/account/email",
    response_class=HTMLResponse,
    response_model=None,
    name="account_change_email",
)
def change_email(
    request: Request,
    new_email: Annotated[str, Form(max_length=320)],
    email_confirmation: Annotated[str, Form(max_length=320)],
    current_password: Annotated[str, Form(max_length=1024)],
    _csrf_valid: Annotated[None, Depends(verify_csrf_token)],
    current_user: User = Depends(require_authenticated_user),
) -> HTMLResponse | RedirectResponse:
    del _csrf_valid
    normalized_email = new_email.strip().casefold()
    confirmed_email = email_confirmation.strip().casefold()
    if len(normalized_email) > 320 or not EMAIL_PATTERN.fullmatch(normalized_email):
        return render_account(
            request,
            current_user=current_user,
            status_code=400,
            email_address=new_email,
            email_error="有効なメールアドレスを入力してください。",
        )
    if normalized_email != confirmed_email:
        return render_account(
            request,
            current_user=current_user,
            status_code=400,
            email_address=new_email,
            email_error="確認用メールアドレスが一致しません。",
        )

    try:
        with Session(get_engine()) as session:
            user = session.scalar(
                select(User).where(
                    User.id == current_user.id,
                    User.is_active.is_(True),
                )
            )
            if user is None:
                raise HTTPException(status_code=404, detail="Account not found")
            if not verify_password(current_password, user.password_hash):
                return render_account(
                    request,
                    current_user=current_user,
                    status_code=400,
                    email_address=new_email,
                    email_error="現在のパスワードを確認してください。",
                )

            existing_user = session.scalar(
                select(User.id).where(
                    User.email == normalized_email,
                    User.id != user.id,
                )
            )
            if existing_user is not None:
                return render_account(
                    request,
                    current_user=current_user,
                    status_code=409,
                    email_address=new_email,
                    email_error="このメールアドレスは既に登録されています。",
                )

            user.email = normalized_email
            session.commit()
    except IntegrityError as error:
        if "uq_users_email_lower" in str(error.orig):
            return render_account(
                request,
                current_user=current_user,
                status_code=409,
                email_address=new_email,
                email_error="このメールアドレスは既に登録されています。",
            )
        logger.exception("Database constraint failed while changing an email")
        return render_account(
            request,
            current_user=current_user,
            status_code=503,
            email_error="メールアドレスを保存できませんでした。",
        )
    except SQLAlchemyError:
        logger.exception("Could not change email for user %s", current_user.id)
        return render_account(
            request,
            current_user=current_user,
            status_code=503,
            email_error="データベースに接続できないか、変更を保存できませんでした。",
        )

    return RedirectResponse("/account?email_updated=1", status_code=303)


@router.post(
    "/account/password",
    response_class=HTMLResponse,
    response_model=None,
    name="account_change_password",
)
def change_password(
    request: Request,
    current_password: Annotated[str, Form(max_length=1024)],
    new_password: Annotated[str, Form(max_length=1024)],
    password_confirmation: Annotated[str, Form(max_length=1024)],
    _csrf_valid: Annotated[None, Depends(verify_csrf_token)],
    current_user: User = Depends(require_authenticated_user),
) -> HTMLResponse | RedirectResponse:
    del _csrf_valid
    if new_password != password_confirmation:
        return render_account(
            request,
            current_user=current_user,
            status_code=400,
            password_error="新しいパスワードが一致しません。",
        )

    try:
        with Session(get_engine()) as session:
            user = session.scalar(
                select(User).where(
                    User.id == current_user.id,
                    User.is_active.is_(True),
                )
            )
            if user is None:
                raise HTTPException(status_code=404, detail="Account not found")
            if not verify_password(current_password, user.password_hash):
                return render_account(
                    request,
                    current_user=current_user,
                    status_code=400,
                    password_error="現在のパスワードを確認してください。",
                )

            try:
                user.password_hash = hash_password(new_password)
            except ValueError:
                return render_account(
                    request,
                    current_user=current_user,
                    status_code=400,
                    password_error="新しいパスワードは12〜1024文字で入力してください。",
                )
            session.commit()
            user_id = user.id
    except SQLAlchemyError:
        logger.exception("Could not change password for user %s", current_user.id)
        return render_account(
            request,
            current_user=current_user,
            status_code=503,
            password_error="データベースに接続できないか、変更を保存できませんでした。",
        )

    request.session.clear()
    request.session[SESSION_USER_KEY] = user_id
    request.session[CSRF_SESSION_KEY] = secrets.token_urlsafe(32)
    return RedirectResponse("/account?password_updated=1", status_code=303)


@router.post(
    "/account/delete",
    response_class=HTMLResponse,
    response_model=None,
    name="account_delete",
)
def delete_account(
    request: Request,
    current_password: Annotated[str, Form(max_length=1024)],
    confirmation_email: Annotated[str, Form(max_length=320)],
    _csrf_valid: Annotated[None, Depends(verify_csrf_token)],
    current_user: User = Depends(require_authenticated_user),
    confirm_delete: Annotated[str | None, Form()] = None,
) -> HTMLResponse | RedirectResponse:
    del _csrf_valid
    if confirm_delete != "yes":
        return render_account(
            request,
            current_user=current_user,
            status_code=400,
            delete_error="退会する場合は確認欄にチェックしてください。",
        )
    if confirmation_email.strip().casefold() != current_user.email:
        return render_account(
            request,
            current_user=current_user,
            status_code=400,
            delete_error="確認用メールアドレスが一致しません。",
        )

    try:
        with Session(get_engine()) as session:
            user = session.scalar(
                select(User).where(
                    User.id == current_user.id,
                    User.is_active.is_(True),
                )
            )
            if user is None:
                raise HTTPException(status_code=404, detail="Account not found")
            if not verify_password(current_password, user.password_hash):
                return render_account(
                    request,
                    current_user=current_user,
                    status_code=400,
                    delete_error="現在のパスワードを確認してください。",
                )

            datasets = session.scalars(
                select(Dataset).where(Dataset.owner_id == user.id)
            ).all()
            histories = session.scalars(
                select(ImportHistory).where(ImportHistory.owner_id == user.id)
            ).all()
            for dataset in datasets:
                session.delete(dataset)
            for history in histories:
                session.delete(history)
            session.delete(user)
            session.commit()
    except SQLAlchemyError:
        logger.exception("Could not delete account for user %s", current_user.id)
        return render_account(
            request,
            current_user=current_user,
            status_code=503,
            delete_error="データベースに接続できないか、退会処理に失敗しました。",
        )

    request.session.clear()
    return RedirectResponse("/login?account_deleted=1", status_code=303)
