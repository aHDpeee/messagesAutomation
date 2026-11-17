import unittest
from unittest.mock import patch

from signal_archive import export_notifications


class ExportNotificationTests(unittest.TestCase):
    def test_linux_notification_uses_argument_vector(self):
        with patch.object(export_notifications.os, "name", "posix"), \
             patch.object(export_notifications.subprocess, "run") as run:
            export_notifications._notify()
        self.assertEqual(run.call_args.args[0][0], "notify-send")
        self.assertTrue(run.call_args.kwargs["start_new_session"])

    def test_windows_notification_is_hidden(self):
        with patch.object(export_notifications.os, "name", "nt"), \
             patch.object(export_notifications.subprocess, "run") as run:
            export_notifications._notify()
        self.assertEqual(run.call_args.args[0][0], "powershell.exe")
        self.assertIn("creationflags", run.call_args.kwargs)

    def test_missing_notification_service_does_not_raise(self):
        with patch.object(export_notifications.os, "name", "posix"), \
             patch.object(export_notifications.subprocess, "run", side_effect=FileNotFoundError):
            export_notifications._notify()
