import re

from httpx import AsyncClient, Response
from sqlalchemy.orm import Session

from app.models import User
from app.security import hash_password

TEST_EMAIL = "importer@example.test"
TEST_PASSWORD = "Phase7-test-password"
TEST_PASSWORD_HASH = hash_password(TEST_PASSWORD)


def add_user(session: Session, *, email: str = TEST_EMAIL, role: str = "importer") -> User:
    user = User(
        email=email,
        password_hash=TEST_PASSWORD_HASH,
        role=role,
    )
    session.add(user)
    session.commit()
    return user


async def sign_in(
    client: AsyncClient,
    *,
    email: str = TEST_EMAIL,
    password: str = TEST_PASSWORD,
    next_url: str = "/",
) -> Response:
    login_page = await client.get("/login", params={"next": next_url})
    token_match = re.search(
        r'name="csrf_token" value="([^"]+)"',
        login_page.text,
    )
    assert token_match is not None
    return await client.post(
        "/login",
        data={
            "email": email,
            "password": password,
            "csrf_token": token_match.group(1),
            "next_url": next_url,
        },
        follow_redirects=False,
    )
