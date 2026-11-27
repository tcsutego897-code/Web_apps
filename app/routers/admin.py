import base64
import binascii
import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_engine
from app.models import User, UserInvitation
from app.security import (
    CSRF_SESSION_KEY,
    SESSION_USER_KEY,
    get_csrf_token,
    hash_password,
    require_admin,
    verify_csrf_token,
)
from app.services.invitations import (
    MAX_INVITATION_CSV_BYTES,
    CSVInvitationRow,
    InvitationCSVError,
    MailConfigurationError,
    MailDeliveryError,
    build_invitation_url,
    parse_invitation_csv,
    send_invitation_email,
    validate_mail_settings,
)
from app.web import templates

router = APIRouter()
logger = logging.getLogger(__name__)
INVITATION_TOKEN_TTL = timedelta(hours=24)
INVITATION_TOKEN_PATTERN_LENGTH = 43


@dataclass(frozen=True)
class PreviewRow:
    row_number: int
    email: str
    message: str
    valid: bool


def render_admin(
    request: Request,
    *,
    users: list[User] | None = None,
    error_message: str | None = None,
    success_message: str | None = None,
    preview_rows: list[PreviewRow] | None = None,
    csv_token: str = "",
    invitation_errors: list[str] | None = None,
    invitation_success_count: int | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="pages/admin_users.html",
        context={
            "app_name": get_settings().app_name,
            "csrf_token": get_csrf_token(request),
            "current_user": getattr(request.state, "current_user", None),
            "users": users or [],
            "error_message": error_message,
            "success_message": success_message,
            "preview_rows": preview_rows,
            "csv_token": csv_token,
            "invitation_errors": invitation_errors or [],
            "invitation_success_count": invitation_success_count,
            "smtp_configured": _mail_is_configured(),
        },
        status_code=status_code,
    )


def load_users() -> list[User]:
    with Session(get_engine()) as session:
        return list(session.scalars(select(User).order_by(User.email)).all())


def _mail_is_configured() -> bool:
    try:
        validate_mail_settings()
    except MailConfigurationError:
        return False
    return True


def _preview_rows(rows: list[CSVInvitationRow]) -> list[PreviewRow]:
    now = datetime.now(timezone.utc)
    addresses = {row.email for row in rows if not row.error}
    if not addresses:
        return [
            PreviewRow(row.row_number, row.email, row.error or "不正な行です。", False)
            for row in rows
        ]

    with Session(get_engine()) as session:
        existing_users = set(
            session.scalars(select(User.email).where(User.email.in_(addresses))).all()
        )
        pending_invitations = set(
            session.scalars(
                select(UserInvitation.email).where(
                    UserInvitation.email.in_(addresses),
                    UserInvitation.expires_at > now,
                )
            ).all()
        )

    seen: set[str] = set()
    result: list[PreviewRow] = []
    for row in rows:
        message = row.error or ""
        valid = row.error is None
        if valid and row.email in seen:
            message = "CSV内でメールアドレスが重複しています。"
            valid = False
        elif valid and row.email in existing_users:
            message = "登録済みのメールアドレスです。"
            valid = False
        elif valid and row.email in pending_invitations:
            message = "有効な招待が既にあります。"
            valid = False
        if row.email:
            seen.add(row.email)
        result.append(
            PreviewRow(
                row.row_number,
                row.email,
                message or "招待メールを送信します。",
                valid,
            )
        )
    return result


@router.get("/admin/users", response_class=HTMLResponse, name="admin_users")
def admin_users_page(
    request: Request,
    _admin: User = Depends(require_admin),
    notice: str = "",
) -> HTMLResponse:
    del _admin
    try:
        users = load_users()
    except SQLAlchemyError:
        logger.exception("Could not load users in the admin screen")
        return render_admin(
            request,
            error_message="会員一覧を読み込めませんでした。",
            status_code=503,
        )
    return render_admin(
        request,
        users=users,
        success_message=notice or None,
    )


@router.post(
    "/admin/users/{user_id}/role",
    response_class=HTMLResponse,
    response_model=None,
    name="admin_change_role",
)
def change_user_role(
    request: Request,
    user_id: int,
    role: Annotated[str, Form()],
    _csrf_valid: Annotated[None, Depends(verify_csrf_token)],
    admin: User = Depends(require_admin),
) -> HTMLResponse | RedirectResponse:
    del _csrf_valid
    if role not in {"viewer", "importer"}:
        raise HTTPException(status_code=400, detail="指定された権限は変更できません。")
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="自分の権限は変更できません。")

    try:
        with Session(get_engine()) as session:
            target = session.scalar(select(User).where(User.id == user_id))
            if target is None:
                raise HTTPException(status_code=404, detail="会員が見つかりません。")
            if target.role == "admin":
                raise HTTPException(
                    status_code=403,
                    detail="管理者権限はCLIからのみ設定できます。",
                )
            target.role = role
            session.commit()
    except HTTPException:
        raise
    except SQLAlchemyError:
        logger.exception("Could not change role for user %s", user_id)
        return render_admin(
            request,
            error_message="会員権限を変更できませんでした。",
            status_code=503,
        )
    return RedirectResponse("/admin/users?notice=会員権限を変更しました。", status_code=303)


@router.post(
    "/admin/users/{user_id}/active",
    response_class=HTMLResponse,
    response_model=None,
    name="admin_change_active",
)
def change_user_active(
    request: Request,
    user_id: int,
    action: Annotated[str, Form()],
    _csrf_valid: Annotated[None, Depends(verify_csrf_token)],
    admin: User = Depends(require_admin),
) -> HTMLResponse | RedirectResponse:
    del _csrf_valid
    if action not in {"activate", "deactivate"}:
        raise HTTPException(status_code=400, detail="指定された操作は実行できません。")
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="自分のアカウント状態は変更できません。")

    try:
        with Session(get_engine()) as session:
            target = session.scalar(select(User).where(User.id == user_id))
            if target is None:
                raise HTTPException(status_code=404, detail="会員が見つかりません。")
            if target.role == "admin":
                raise HTTPException(
                    status_code=403,
                    detail="管理者アカウントの状態は管理画面から変更できません。",
                )
            target.is_active = action == "activate"
            session.commit()
    except HTTPException:
        raise
    except SQLAlchemyError:
        logger.exception("Could not change active state for user %s", user_id)
        return render_admin(
            request,
            error_message="会員状態を変更できませんでした。",
            status_code=503,
        )
    message = "会員を有効化しました。" if action == "activate" else "会員を停止しました。"
    return RedirectResponse(
        f"/admin/users?notice={message}",
        status_code=303,
    )


@router.post(
    "/admin/users/invitations/preview",
    response_class=HTMLResponse,
    name="admin_invitation_preview",
)
async def invitation_preview(
    request: Request,
    _admin: User = Depends(require_admin),
    _csrf_valid: None = Depends(verify_csrf_token),
    upload: UploadFile | None = File(None),
) -> HTMLResponse:
    del _csrf_valid
    if upload is None or not upload.filename:
        return render_admin(
            request,
            error_message="会員CSVファイルを選択してください。",
            status_code=400,
        )

    content = await upload.read(MAX_INVITATION_CSV_BYTES + 1)
    try:
        rows = parse_invitation_csv(content)
        preview_rows = _preview_rows(rows)
    except InvitationCSVError as error:
        return render_admin(
            request,
            error_message=str(error),
            status_code=400,
        )
    except SQLAlchemyError:
        logger.exception("Could not validate invitation CSV rows")
        return render_admin(
            request,
            error_message="招待対象を確認できませんでした。",
            status_code=503,
        )
    encoded_content = base64.urlsafe_b64encode(content).decode("ascii")
    return render_admin(
        request,
        preview_rows=preview_rows,
        csv_token=encoded_content,
        error_message=(
            "入力エラーを修正してCSVを再アップロードしてください。"
            if any(not row.valid for row in preview_rows)
            else None
        ),
    )


@router.post(
    "/admin/users/invitations/send",
    response_class=HTMLResponse,
    name="admin_invitation_send",
)
def send_invitations(
    request: Request,
    csv_token: Annotated[str, Form(max_length=350_000)],
    _csrf_valid: Annotated[None, Depends(verify_csrf_token)],
    _admin: User = Depends(require_admin),
) -> HTMLResponse:
    del _csrf_valid
    try:
        encoded = csv_token.encode("ascii")
        content = base64.b64decode(encoded, altchars=b"-_", validate=True)
        rows = parse_invitation_csv(content)
        preview_rows = _preview_rows(rows)
    except (UnicodeEncodeError, binascii.Error, InvitationCSVError) as error:
        message = (
            str(error)
            if isinstance(error, InvitationCSVError)
            else "プレビュー情報が正しくありません。CSVを選び直してください。"
        )
        return render_admin(request, error_message=message, status_code=400)
    except SQLAlchemyError:
        logger.exception("Could not validate invitation CSV rows")
        return render_admin(
            request,
            error_message="招待対象を確認できませんでした。",
            status_code=503,
        )
    if any(not row.valid for row in preview_rows):
        return render_admin(
            request,
            preview_rows=preview_rows,
            csv_token=csv_token,
            error_message="重複または入力エラーがあります。招待メールは送信していません。",
            status_code=400,
        )
    try:
        validate_mail_settings()
    except MailConfigurationError as error:
        return render_admin(
            request,
            preview_rows=preview_rows,
            csv_token=csv_token,
            error_message=str(error),
            status_code=503,
        )

    tokens = [(row.email, secrets.token_urlsafe(32)) for row in rows]
    invitations = [
        UserInvitation(
            email=email,
            role="viewer",
            token_hash=hashlib.sha256(token.encode("ascii")).hexdigest(),
            expires_at=datetime.now(timezone.utc) + INVITATION_TOKEN_TTL,
        )
        for email, token in tokens
    ]
    try:
        with Session(get_engine()) as session:
            session.execute(
                delete(UserInvitation).where(
                    UserInvitation.expires_at <= datetime.now(timezone.utc)
                )
            )
            session.add_all(invitations)
            session.commit()
    except IntegrityError:
        logger.info("Invitation addresses changed while processing a CSV")
        return render_admin(
            request,
            error_message=(
                "処理中に登録状態が変わりました。ページを再読み込みして確認してください。"
            ),
            status_code=409,
        )
    except SQLAlchemyError:
        logger.exception("Could not create invitations from CSV")
        return render_admin(
            request,
            error_message="招待情報を保存できませんでした。",
            status_code=503,
        )

    sent_count = 0
    invitation_errors: list[str] = []
    failed_emails: set[str] = set()
    for index, (email, token) in enumerate(tokens):
        try:
            invitation_url = build_invitation_url(token)
            send_invitation_email(email, invitation_url)
            sent_count += 1
        except MailConfigurationError as error:
            invitation_errors.append(str(error))
            failed_emails.update(
                remaining_email for remaining_email, _token in tokens[index:]
            )
            break
        except MailDeliveryError:
            failed_emails.add(email)
            invitation_errors.append(f"{email}: 招待メールを送信できませんでした。")

    if failed_emails:
        try:
            with Session(get_engine()) as session:
                session.execute(
                    delete(UserInvitation).where(
                        UserInvitation.email.in_(failed_emails)
                    )
                )
                session.commit()
        except SQLAlchemyError:
            logger.exception("Could not revoke invitations for unsent emails")
            invitation_errors.append(
                "未送信分の招待を無効化できませんでした。管理者がDBを確認してください。"
            )
        invitation_errors.append(f"未送信: {len(failed_emails)}件")

    try:
        users = load_users()
    except SQLAlchemyError:
        logger.exception("Could not reload users after sending invitations")
        users = []
    return render_admin(
        request,
        users=users,
        success_message=(
            f"{sent_count}件の招待メールを送信しました。" if sent_count else None
        ),
        invitation_errors=invitation_errors,
        invitation_success_count=sent_count,
    )


def _valid_invitation(token: str) -> UserInvitation | None:
    if len(token) != INVITATION_TOKEN_PATTERN_LENGTH:
        return None
    token_hash = hashlib.sha256(token.encode("ascii", errors="ignore")).hexdigest()
    with Session(get_engine()) as session:
        invitation = session.scalar(
            select(UserInvitation).where(UserInvitation.token_hash == token_hash)
        )
        if invitation is None:
            return None
        expiration = invitation.expires_at
        if expiration.tzinfo is None:
            expiration = expiration.replace(tzinfo=timezone.utc)
        if expiration <= datetime.now(timezone.utc):
            return None
        return invitation


def render_invitation_accept(
    request: Request,
    *,
    token: str,
    email: str | None = None,
    error_message: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="pages/invitation_accept.html",
        context={
            "app_name": get_settings().app_name,
            "csrf_token": get_csrf_token(request),
            "current_user": getattr(request.state, "current_user", None),
            "token": token,
            "email": email,
            "error_message": error_message,
        },
        status_code=status_code,
    )


@router.get(
    "/invitations/accept",
    response_class=HTMLResponse,
    name="invitation_accept",
)
def invitation_accept_page(request: Request, token: str = "") -> HTMLResponse:
    try:
        invitation = _valid_invitation(token)
    except SQLAlchemyError:
        logger.exception("Could not validate an invitation token")
        return render_invitation_accept(
            request,
            token=token,
            error_message="招待を確認できませんでした。",
            status_code=503,
        )
    if invitation is None:
        return render_invitation_accept(
            request,
            token="",
            error_message="招待リンクは無効か、有効期限が切れています。",
            status_code=400,
        )
    return render_invitation_accept(
        request,
        token=token,
        email=invitation.email,
    )


@router.post(
    "/invitations/accept",
    response_class=HTMLResponse,
    response_model=None,
    name="invitation_accept_submit",
)
def accept_invitation(
    request: Request,
    token: Annotated[str, Form()],
    password: Annotated[str, Form(max_length=1024)],
    password_confirmation: Annotated[str, Form(max_length=1024)],
    _csrf_valid: Annotated[None, Depends(verify_csrf_token)],
) -> HTMLResponse | RedirectResponse:
    del _csrf_valid
    invited_email: str | None = None
    try:
        with Session(get_engine()) as session:
            token_hash = hashlib.sha256(
                token.encode("ascii", errors="ignore")
            ).hexdigest()
            invitation = session.scalar(
                select(UserInvitation).where(
                    UserInvitation.token_hash == token_hash
                )
            )
            if invitation is None:
                return render_invitation_accept(
                    request,
                    token="",
                    error_message="招待リンクは無効か、既に使用されています。",
                    status_code=400,
                )
            expiration = invitation.expires_at
            if expiration.tzinfo is None:
                expiration = expiration.replace(tzinfo=timezone.utc)
            if expiration <= datetime.now(timezone.utc):
                return render_invitation_accept(
                    request,
                    token="",
                    error_message="招待リンクは無効か、有効期限が切れています。",
                    status_code=400,
                )
            invited_email = invitation.email
            if password != password_confirmation:
                return render_invitation_accept(
                    request,
                    token=token,
                    email=invited_email,
                    error_message="パスワードが一致しません。",
                    status_code=400,
                )
            try:
                password_hash = hash_password(password)
            except ValueError:
                return render_invitation_accept(
                    request,
                    token=token,
                    email=invited_email,
                    error_message="パスワードは12〜1024文字で入力してください。",
                    status_code=400,
                )
            user = User(
                email=invitation.email,
                password_hash=password_hash,
                role=invitation.role,
            )
            session.add(user)
            session.flush()
            user_id = user.id
            session.delete(invitation)
            session.commit()
    except IntegrityError:
        return render_invitation_accept(
            request,
            token=token,
            email=invited_email,
            error_message="このメールアドレスは既に登録されています。",
            status_code=409,
        )
    except SQLAlchemyError:
        logger.exception("Could not accept an account invitation")
        return render_invitation_accept(
            request,
            token=token,
            error_message="アカウントを作成できませんでした。",
            status_code=503,
        )

    request.session.clear()
    request.session[SESSION_USER_KEY] = user_id
    request.session[CSRF_SESSION_KEY] = secrets.token_urlsafe(32)
    return RedirectResponse("/", status_code=303)
