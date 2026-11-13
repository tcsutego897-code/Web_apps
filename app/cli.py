import argparse
import getpass
import sys

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_engine
from app.models import User
from app.security import hash_password


def create_user(email: str, role: str) -> int:
    password = getpass.getpass("Password (12-1024 characters): ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        print("Passwords do not match.", file=sys.stderr)
        return 1

    try:
        password_hash = hash_password(password)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1

    try:
        with Session(get_engine()) as session:
            session.add(
                User(
                    email=email,
                    password_hash=password_hash,
                    role=role,
                )
            )
            session.commit()
    except IntegrityError:
        print("That email address is already registered.", file=sys.stderr)
        return 1
    except SQLAlchemyError:
        print(
            "The database operation failed. Check the database configuration.",
            file=sys.stderr,
        )
        return 1

    print(f"Created {role} account for {email.strip().casefold()}.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage Akashic Record accounts.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create_parser = subparsers.add_parser(
        "create-user",
        help="Create a viewer or importer account.",
    )
    create_parser.add_argument("--email", required=True)
    create_parser.add_argument(
        "--role",
        choices=("viewer", "importer"),
        required=True,
    )
    arguments = parser.parse_args()

    if not arguments.email.strip() or len(arguments.email) > 320:
        parser.error("email must contain 1 to 320 characters")
    return create_user(arguments.email, arguments.role)


if __name__ == "__main__":
    raise SystemExit(main())
