"""Best-effort native desktop notification after an export completes."""
from __future__ import annotations

import os
import subprocess
import threading


def _notify() -> None:
    try:
        if os.name == "nt":
            # A constant script avoids interpolating paths or Telegram content
            # into PowerShell. Run hidden so the windowed app stays windowed.
            script = """
[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom.XmlDocument,ContentType=WindowsRuntime] > $null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml('<toast><visual><binding template="ToastGeneric"><text>Signal Archive</text><text>Export completed. Open the application to see the result.</text></binding></visual></toast>')
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Signal Archive').Show($toast)
"""
            subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
        else:
            subprocess.run(["notify-send", "Signal Archive", "Export completed. Open the application to see the result."],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           start_new_session=True, check=False)
    except (OSError, RuntimeError):
        # Notifications are optional and must not affect completed exports.
        pass


def notify_export_completed() -> None:
    """Send asynchronously; missing desktop notification support is harmless."""
    try:
        threading.Thread(target=_notify, name="export-notification", daemon=True).start()
    except RuntimeError:
        pass
