"""Send an explicitly configured archive using authenticated TLS SMTP."""
from email.message import EmailMessage
from pathlib import Path
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime


def send(host: str, port: int, username: str, password: str, recipient: str, path: str, filename: str, retention_days: int = 0) -> None:
    if not host or port not in (465, 587) or not username or not password or not recipient:
        raise ValueError("SMTP configuration is incomplete")
    if any("\n" in value or "\r" in value for value in (host, username, recipient, filename)):
        raise ValueError("Invalid SMTP configuration")
    attachment = Path(path)
    if not attachment.is_file() or attachment.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("Archive exceeds the 20 MiB email limit")
    message = EmailMessage()
    message["From"] = username
    message["To"] = recipient
    message["Subject"] = "[Tegash Archive] Автоматический архив сообщений"
    message["X-Tegash-Archive"] = "scheduled-export"
    message["Date"] = format_datetime(datetime.now(timezone.utc))
    if 1 <= retention_days <= 36500:
        message["X-Tegash-Retention-Days"] = str(retention_days)
        message["Expires"] = format_datetime(datetime.now(timezone.utc) + timedelta(days=retention_days))
    message.set_content("Во вложении — архив Tegash, отправленный по вашему расписанию.\n\nМетка для почтового фильтра: [Tegash Archive].")
    message.add_attachment(attachment.read_bytes(), maintype="application", subtype="zip", filename=filename)
    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=30, context=context) as client:
            client.login(username, password)
            client.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=30) as client:
            client.ehlo()
            client.starttls(context=context)
            client.ehlo()
            client.login(username, password)
            client.send_message(message)
