"""
Отправка ZIP с заявкой по SMTP (Mail.ru, Gmail и др.). Пароли только в переменных окружения.

Универсально:
  MAIL_USER   — логин (полный e-mail)
  MAIL_PASSWORD — пароль или пароль приложения
  MAIL_TO     — кому (если не задан — письмо на MAIL_USER)

Устаревшие имена (совместимость): GMAIL_USER, GMAIL_APP_PASSWORD

Сервер SMTP (если не задан SMTP_HOST):
  @mail.ru / @bk.ru / @inbox.ru → smtp.mail.ru:465 SSL
  @gmail.com → smtp.gmail.com:465 SSL
  иначе задайте: SMTP_HOST, SMTP_PORT (по умолчанию 465), SMTP_SSL=1

Пример Mail.ru (PowerShell из корня pass_automation):
  $env:MAIL_USER="ваш@mail.ru"
  $env:MAIL_PASSWORD="пароль"
  $env:MAIL_TO="получатель@example.com"
  python scripts/send_email.py

Пример Gmail (пароль приложения):
  $env:GMAIL_USER="ваш@gmail.com"
  $env:GMAIL_APP_PASSWORD="xxxx xxxx xxxx xxxx"
  python scripts/send_email.py
"""

from __future__ import annotations

import os
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.config import BASE_DIR  # noqa: E402

# Типичный лимит вложений у почтовых сервисов ~25 МБ
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024

DEFAULT_ZIP = BASE_DIR / "data" / "output" / "1_Farzaliev.zip"


def _mail_credentials() -> tuple[str, str]:
    user = (os.environ.get("MAIL_USER") or os.environ.get("GMAIL_USER", "")).strip()
    password = os.environ.get("MAIL_PASSWORD") or os.environ.get("GMAIL_APP_PASSWORD", "")
    password = password.replace(" ", "")
    return user, password


def _default_smtp_for_email(email: str) -> tuple[str, int]:
    lower = email.lower()
    if lower.endswith("@gmail.com"):
        return "smtp.gmail.com", 465
    if any(
        lower.endswith(s)
        for s in ("@mail.ru", "@bk.ru", "@inbox.ru", "@list.ru", "@internet.ru")
    ):
        return "smtp.mail.ru", 465
    return "", 465


def _resolve_smtp(email_user: str) -> tuple[str, int, bool]:
    host = os.environ.get("SMTP_HOST", "").strip()
    port_str = os.environ.get("SMTP_PORT", "").strip()
    ssl_str = os.environ.get("SMTP_SSL", "1").strip().lower()
    use_ssl = ssl_str not in ("0", "false", "no")

    if host:
        port = int(port_str or "465")
        return host, port, use_ssl

    h, default_port = _default_smtp_for_email(email_user)
    if not h:
        raise SystemExit(
            "Задайте SMTP_HOST (и при необходимости SMTP_PORT), "
            "или используйте ящик @mail.ru / @gmail.com для авто-выбора сервера."
        )
    port = int(port_str or str(default_port))
    return h, port, True


def main() -> None:
    if len(sys.argv) > 1:
        zip_path = Path(sys.argv[1]).expanduser().resolve()
    else:
        zip_path = DEFAULT_ZIP.resolve()

    email_user, app_password = _mail_credentials()
    to_email = os.environ.get("MAIL_TO", email_user).strip()

    if not email_user or not app_password:
        raise SystemExit(
            "Задайте MAIL_USER и MAIL_PASSWORD (или GMAIL_USER и GMAIL_APP_PASSWORD для Gmail)."
        )
    if not to_email:
        raise SystemExit("Задайте MAIL_TO или отправка на тот же адрес, что в MAIL_USER.")
    if not zip_path.is_file():
        raise SystemExit(f"Файл не найден: {zip_path}")

    size = zip_path.stat().st_size
    if size > MAX_ATTACHMENT_BYTES:
        raise SystemExit(
            f"Файл слишком большой для типичной почты (лимит ~25 МБ): "
            f"{size} байт ({zip_path.name})"
        )

    smtp_host, smtp_port, use_ssl = _resolve_smtp(email_user)

    msg = EmailMessage()
    msg["Subject"] = "Заявка на пропуск"
    msg["From"] = email_user
    msg["To"] = to_email
    msg.set_content(
        """Добрый день.

Направляем заявку на оформление пропуска.

Файл во вложении.

С уважением.
"""
    )

    file_data = zip_path.read_bytes()
    msg.add_attachment(
        file_data,
        maintype="application",
        subtype="zip",
        filename=zip_path.name,
    )

    try:
        if use_ssl:
            smtp_cm = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=60)
        else:
            smtp_cm = smtplib.SMTP(smtp_host, smtp_port, timeout=60)

        with smtp_cm as smtp:
            if not use_ssl:
                smtp.starttls()
            smtp.login(email_user, app_password)
            smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        raise SystemExit(
            "Ошибка входа SMTP: проверьте MAIL_USER / MAIL_PASSWORD "
            "(для Gmail — пароль приложения, не обычный пароль)."
        ) from e
    except smtplib.SMTPException as e:
        raise SystemExit(f"Ошибка SMTP: {e}") from e
    except OSError as e:
        raise SystemExit(f"Сеть или соединение: {e}") from e

    print(
        "Письмо отправлено:",
        to_email,
        "| вложение:",
        zip_path.name,
        "| SMTP:",
        f"{smtp_host}:{smtp_port}",
    )


if __name__ == "__main__":
    main()
