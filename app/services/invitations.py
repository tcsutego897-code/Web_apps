import csv
import io
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from urllib.parse import urlsplit

from app.config import get_settings

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_INVITATION_CSV_BYTES = 256 * 1024
MAX_INVITATION_CSV_ROWS = 100


@dataclass(frozen=True)
class CSVInvitationRow:
    row_number: int
    email: str
    error: str | None = None


class InvitationCSVError(ValueError):
    """A safe, user-facing invitation CSV validation error."""


class MailConfigurationError(ValueError):
    """The invitation email settings are incomplete or invalid."""


class MailDeliveryError(RuntimeError):
    """The invitation email could not be delivered."""


def parse_invitation_csv(content: bytes) -> list[CSVInvitationRow]:
    if not content:
        raise InvitationCSVError("CSVファイルが空です。")
    if len(content) > MAX_INVITATION_CSV_BYTES:
        raise InvitationCSVError("CSVファイルは256 KiB以下にしてください。")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise InvitationCSVError(
            "CSVはUTF-8形式で保存してください。"
        ) from error

    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        header = next(reader)
    except StopIteration as error:
        raise InvitationCSVError("CSVに見出し行がありません。") from error
    except csv.Error as error:
        raise InvitationCSVError("CSVの形式を確認してください。") from error

    if len(header) != 1 or header[0].strip().casefold() != "email":
        raise InvitationCSVError("見出しは email の1列にしてください。")

    parsed_rows: list[CSVInvitationRow] = []
    try:
        for values in reader:
            row_number = reader.line_num
            if not values or all(not value.strip() for value in values):
                continue
            if len(parsed_rows) >= MAX_INVITATION_CSV_ROWS:
                raise InvitationCSVError(
                    f"一度に登録できるのは{MAX_INVITATION_CSV_ROWS}件までです。"
                )
            if len(values) != 1:
                parsed_rows.append(
                    CSVInvitationRow(
                        row_number,
                        "",
                        "列数が正しくありません。",
                    )
                )
                continue
            email = values[0].strip().casefold()
            if len(email) > 320 or not EMAIL_PATTERN.fullmatch(email):
                parsed_rows.append(
                    CSVInvitationRow(
                        row_number,
                        email,
                        "有効なメールアドレスではありません。",
                    )
                )
                continue
            parsed_rows.append(CSVInvitationRow(row_number, email))
    except csv.Error as error:
        raise InvitationCSVError("CSVの形式を確認してください。") from error

    if not parsed_rows:
        raise InvitationCSVError("招待するメールアドレスがありません。")
    return parsed_rows


def build_invitation_url(token: str) -> str:
    settings = get_settings()
    parsed_url = urlsplit(settings.public_base_url)
    if (
        parsed_url.scheme not in {"http", "https"}
        or not parsed_url.netloc
        or parsed_url.username is not None
        or parsed_url.password is not None
        or parsed_url.query
        or parsed_url.fragment
        or (
            settings.environment.casefold() in {"production", "prod"}
            and parsed_url.scheme != "https"
        )
    ):
        raise MailConfigurationError(
            "招待メール用の公開URL（PUBLIC_BASE_URL）は有効なHTTPS URLにしてください。"
        )
    return f"{settings.public_base_url.rstrip('/')}/invitations/accept?token={token}"


def validate_mail_settings() -> None:
    settings = get_settings()
    if not settings.smtp_host or not settings.smtp_from_email:
        raise MailConfigurationError(
            "招待メール設定（SMTP_HOST、SMTP_FROM_EMAIL）が不足しています。"
        )
    build_invitation_url("configuration-check")
    if bool(settings.smtp_username) != bool(settings.smtp_password):
        raise MailConfigurationError(
            "SMTP_USERNAMEとSMTP_PASSWORDは両方設定してください。"
        )
    if (
        settings.environment.casefold() in {"production", "prod"}
        and not settings.smtp_starttls
    ):
        raise MailConfigurationError(
            "本番環境ではSMTP_STARTTLSを有効にしてください。"
        )
    if not EMAIL_PATTERN.fullmatch(settings.smtp_from_email):
        raise MailConfigurationError("SMTP_FROM_EMAILの形式を確認してください。")


def send_invitation_email(email: str, invitation_url: str) -> None:
    validate_mail_settings()
    settings = get_settings()
    message = EmailMessage()
    message["Subject"] = "Akashic Record アカウントのご案内"
    message["From"] = settings.smtp_from_email
    message["To"] = email
    message.set_content(
        "Akashic Recordのアカウントが招待されました。\n"
        "次のリンクから24時間以内にパスワードを設定してください。\n\n"
        f"{invitation_url}\n"
    )
    try:
        with smtplib.SMTP(
            settings.smtp_host,
            settings.smtp_port,
            timeout=15,
        ) as server:
            server.ehlo()
            if settings.smtp_starttls:
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
            if settings.smtp_username:
                server.login(settings.smtp_username, settings.smtp_password)
            server.send_message(message)
    except (OSError, smtplib.SMTPException) as error:
        raise MailDeliveryError("招待メールを送信できませんでした。") from error
