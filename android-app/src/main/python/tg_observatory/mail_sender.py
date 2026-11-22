"""Send an explicitly configured archive using authenticated TLS SMTP."""
from email.message import EmailMessage
from pathlib import Path
import smtplib
import ssl


def send(host: str, port: int, username: str, password: str, recipient: str, path: str, filename: str) -> None:
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
    message["Subject"] = "Tegash: архив сообщений"
    message.set_content("Во вложении — архив Tegash, отправленный по вашему расписанию.")
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
