import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tg_observatory.mail_sender import send


class MailSenderTests(unittest.TestCase):
    def test_starttls_and_attachment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "archive.zip"
            path.write_bytes(b"archive")
            client = MagicMock()
            with patch("tg_observatory.mail_sender.smtplib.SMTP") as smtp:
                smtp.return_value.__enter__.return_value = client
                send("smtp.example.com", 587, "from@example.com", "secret", "to@example.com", str(path), "archive.zip")
            client.starttls.assert_called_once()
            client.login.assert_called_once_with("from@example.com", "secret")
            message = client.send_message.call_args.args[0]
            self.assertEqual(message["To"], "to@example.com")
            self.assertEqual(next(message.iter_attachments()).get_payload(decode=True), b"archive")

    def test_invalid_port_never_connects(self):
        with patch("tg_observatory.mail_sender.smtplib.SMTP") as smtp:
            with self.assertRaises(ValueError):
                send("smtp.example.com", 25, "user", "secret", "to@example.com", "/unused", "archive.zip")
            smtp.assert_not_called()
