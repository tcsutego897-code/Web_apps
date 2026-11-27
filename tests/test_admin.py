import asyncio
import hashlib
import re
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.database import Base
from app.main import app
from app.models import User, UserInvitation
from app.security import verify_password
from app.services.invitations import MailDeliveryError
from tests.security_helpers import add_user, sign_in

ADMIN_EMAIL = "admin@example.test"
INVITEE_EMAIL = "invitee@example.test"
INVITEE_PASSWORD = "An-invited-secure-password"


@pytest.fixture
def admin_database(tmp_path: Path) -> Iterator[tuple[Engine, int, int]]:
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'admin.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        admin = add_user(session, email=ADMIN_EMAIL, role="admin")
        viewer = add_user(session, email="viewer@example.test", role="viewer")
        admin_id = admin.id
        viewer_id = viewer.id
    yield engine, admin_id, viewer_id
    engine.dispose()


async def _admin_request(
    path: str,
    *,
    method: str = "GET",
    email: str = ADMIN_EMAIL,
    data: dict[str, str] | None = None,
    files: dict[str, tuple[str, bytes, str]] | None = None,
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as client:
        await sign_in(client, email=email)
        if method == "GET":
            return await client.get(path)

        page = await client.get("/admin/users")
        token_match = re.search(
            r'name="csrf_token" value="([^"]+)"',
            page.text,
        )
        assert token_match is not None
        post_data = dict(data or {})
        post_data["csrf_token"] = token_match.group(1)
        return await client.post(path, data=post_data, files=files)


def _request(
    path: str,
    *,
    method: str = "GET",
    email: str = ADMIN_EMAIL,
    data: dict[str, str] | None = None,
    files: dict[str, tuple[str, bytes, str]] | None = None,
) -> httpx.Response:
    return asyncio.run(
        _admin_request(
            path,
            method=method,
            email=email,
            data=data,
            files=files,
        )
    )


def test_admin_screen_requires_admin_role(
    admin_database: tuple[Engine, int, int],
) -> None:
    engine, _admin_id, _viewer_id = admin_database
    with (
        patch("app.routers.auth.get_engine", return_value=engine),
        patch("app.routers.admin.get_engine", return_value=engine),
        patch("app.security.get_engine", return_value=engine),
    ):
        forbidden = _request("/admin/users", email="viewer@example.test")
        page = _request("/admin/users")

    assert forbidden.status_code == 403
    assert page.status_code == 200
    assert "会員管理" in page.text
    assert ADMIN_EMAIL in page.text
    assert "管理者（CLI設定）" in page.text


def test_admin_can_change_member_role_but_cannot_grant_admin_or_edit_self(
    admin_database: tuple[Engine, int, int],
) -> None:
    engine, admin_id, viewer_id = admin_database
    with (
        patch("app.routers.auth.get_engine", return_value=engine),
        patch("app.routers.admin.get_engine", return_value=engine),
        patch("app.security.get_engine", return_value=engine),
    ):
        changed = _request(
            f"/admin/users/{viewer_id}/role",
            method="POST",
            data={"role": "importer"},
        )
        escalation = _request(
            f"/admin/users/{viewer_id}/role",
            method="POST",
            data={"role": "admin"},
        )
        self_change = _request(
            f"/admin/users/{admin_id}/role",
            method="POST",
            data={"role": "viewer"},
        )
        deactivate = _request(
            f"/admin/users/{viewer_id}/active",
            method="POST",
            data={"action": "deactivate"},
        )
        self_deactivate = _request(
            f"/admin/users/{admin_id}/active",
            method="POST",
            data={"action": "deactivate"},
        )

    assert changed.status_code == 303
    assert escalation.status_code == 400
    assert self_change.status_code == 400
    assert deactivate.status_code == 303
    assert self_deactivate.status_code == 400
    with Session(engine) as session:
        assert session.get(User, viewer_id).role == "importer"
        assert session.get(User, viewer_id).is_active is False
        assert session.get(User, admin_id).role == "admin"


def test_invitation_csv_preview_reports_duplicates_before_sending(
    admin_database: tuple[Engine, int, int],
) -> None:
    engine, _admin_id, _viewer_id = admin_database
    csv_content = (
        b"email\nviewer@example.test\nnew@example.test\nNEW@example.test\nbad-address\n"
    )
    with (
        patch("app.routers.auth.get_engine", return_value=engine),
        patch("app.routers.admin.get_engine", return_value=engine),
        patch("app.security.get_engine", return_value=engine),
    ):
        response = _request(
            "/admin/users/invitations/preview",
            method="POST",
            files={"upload": ("members.csv", csv_content, "text/csv")},
        )

    assert response.status_code == 200
    assert "登録済みのメールアドレスです" in response.text
    assert "CSV内でメールアドレスが重複しています" in response.text
    assert "有効なメールアドレスではありません" in response.text
    assert 'name="csv_token"' not in response.text
    with Session(engine) as session:
        assert session.scalar(select(UserInvitation.id)) is None


def test_invitation_sets_password_and_cannot_be_reused(
    admin_database: tuple[Engine, int, int],
) -> None:
    engine, _admin_id, _viewer_id = admin_database
    mail_sender = patch("app.routers.admin.send_invitation_email")
    with (
        patch("app.routers.auth.get_engine", return_value=engine),
        patch("app.routers.admin.get_engine", return_value=engine),
        patch("app.security.get_engine", return_value=engine),
        patch("app.routers.admin.validate_mail_settings"),
        patch(
            "app.routers.admin.build_invitation_url",
            side_effect=lambda token: (
                f"https://example.test/invitations/accept?token={token}"
            ),
        ),
        mail_sender as send_email,
    ):

        async def run_flow() -> tuple[
            httpx.Response,
            httpx.Response,
            httpx.Response,
            httpx.Response,
        ]:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
                follow_redirects=False,
            ) as client:
                await sign_in(client, email=ADMIN_EMAIL)
                admin_page = await client.get("/admin/users")
                csrf_match = re.search(
                    r'name="csrf_token" value="([^"]+)"',
                    admin_page.text,
                )
                assert csrf_match is not None
                preview = await client.post(
                    "/admin/users/invitations/preview",
                    data={"csrf_token": csrf_match.group(1)},
                    files={
                        "upload": (
                            "members.csv",
                            f"email\n{INVITEE_EMAIL}\n".encode(),
                            "text/csv",
                        )
                    },
                )
                csv_token_match = re.search(
                    r'name="csv_token" value="([^"]+)"',
                    preview.text,
                )
                assert csv_token_match is not None
                sent = await client.post(
                    "/admin/users/invitations/send",
                    data={
                        "csrf_token": csrf_match.group(1),
                        "csv_token": csv_token_match.group(1),
                    },
                )
                invitation_url = send_email.call_args.args[1]
                token = invitation_url.split("token=", 1)[1]
                accept_page = await client.get(
                    "/invitations/accept",
                    params={"token": token},
                )
                accept_csrf_match = re.search(
                    r'name="csrf_token" value="([^"]+)"',
                    accept_page.text,
                )
                assert accept_csrf_match is not None
                mismatch = await client.post(
                    "/invitations/accept",
                    data={
                        "csrf_token": accept_csrf_match.group(1),
                        "token": token,
                        "password": INVITEE_PASSWORD,
                        "password_confirmation": "a-different-password",
                    },
                )
                accepted = await client.post(
                    "/invitations/accept",
                    data={
                        "csrf_token": accept_csrf_match.group(1),
                        "token": token,
                        "password": INVITEE_PASSWORD,
                        "password_confirmation": INVITEE_PASSWORD,
                    },
                )
                reused = await client.get(
                    "/invitations/accept",
                    params={"token": token},
                )
                return sent, mismatch, accepted, reused

        sent, mismatch, accepted, reused = asyncio.run(run_flow())

    assert sent.status_code == 200
    assert "1件の招待メールを送信しました" in sent.text
    assert mismatch.status_code == 400
    assert 'name="password"' in mismatch.text
    assert "パスワードが一致しません" in mismatch.text
    assert accepted.status_code == 303
    assert reused.status_code == 400
    send_email.assert_called_once()
    with Session(engine) as session:
        invitee = session.scalar(select(User).where(User.email == INVITEE_EMAIL))
        assert invitee is not None
        assert invitee.role == "viewer"
        assert verify_password(INVITEE_PASSWORD, invitee.password_hash)
        assert session.scalar(select(UserInvitation.id)) is None


def test_expired_invitation_is_rejected(
    admin_database: tuple[Engine, int, int],
) -> None:
    engine, _admin_id, _viewer_id = admin_database
    token = "expired-invitation-token-123456789012"
    token = (token + "x" * 43)[:43]
    with Session(engine) as session:
        session.add(
            UserInvitation(
                email=INVITEE_EMAIL,
                role="viewer",
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            )
        )
        session.commit()

    with (
        patch("app.routers.admin.get_engine", return_value=engine),
    ):
        response = asyncio.run(_get_expired_invitation(token))

    assert response.status_code == 400
    assert "有効期限が切れています" in response.text
    with Session(engine) as session:
        assert session.scalar(select(User).where(User.email == INVITEE_EMAIL)) is None


async def _get_expired_invitation(token: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as client:
        return await client.get("/invitations/accept", params={"token": token})


def test_failed_invitation_email_is_revoked(
    admin_database: tuple[Engine, int, int],
) -> None:
    engine, _admin_id, _viewer_id = admin_database
    with (
        patch("app.routers.auth.get_engine", return_value=engine),
        patch("app.routers.admin.get_engine", return_value=engine),
        patch("app.security.get_engine", return_value=engine),
        patch("app.routers.admin.validate_mail_settings"),
        patch(
            "app.routers.admin.build_invitation_url",
            return_value="https://example.test/invitations/accept?token=redacted",
        ),
        patch(
            "app.routers.admin.send_invitation_email",
            side_effect=MailDeliveryError("not shown"),
        ),
    ):
        response = _request(
            "/admin/users/invitations/preview",
            method="POST",
            files={
                "upload": (
                    "members.csv",
                    f"email\n{INVITEE_EMAIL}\n".encode(),
                    "text/csv",
                )
            },
        )
        csv_token_match = re.search(
            r'name="csv_token" value="([^"]+)"',
            response.text,
        )
        assert csv_token_match is not None
        response = _request(
            "/admin/users/invitations/send",
            method="POST",
            data={"csv_token": csv_token_match.group(1)},
        )

    assert response.status_code == 200
    assert f"{INVITEE_EMAIL}: 招待メールを送信できませんでした" in response.text
    assert "1件" in response.text
    with Session(engine) as session:
        assert session.scalar(
            select(UserInvitation.id).where(
                UserInvitation.email == INVITEE_EMAIL
            )
        ) is None
