# Development plan and release checklist

## Scope and current state

The target is a local desktop application for Linux and Windows. It lists the user's Telegram dialogs, exports selected chats regularly as text and metadata without media, and exports visible group members only when the user chooses that action in the interface. The new direct workspace uses its own Telethon session and does not modify the older Telegram Archive installation.

The direct workspace, selection, JSONL exports, member CSV, schedule, optional sign-in startup, cooldown handling, and installer scripts are implemented. Local tests use fake network responses and some real Telethon iterator objects. A ZIP can be built with `python desktop/package_direct.py`. These facts do **not** establish that the app works with a live account or on Windows. The direct workspace remains experimental.

## Step 1 — Run local checks

1. Use Python 3.10 or newer with Telethon 1.45.0 and keyring 25.7.0 installed. Do not point the test process at a real Telegram session.
2. From the repository root on Linux, run `SIGNAL_ARCHIVE_PYTHON=/absolute/path/to/python ./desktop/verify-release.sh`.
3. Confirm the command exits 0 and reports no failures. If it reports missing dependencies or skips Telegram tests, stop and fix the environment.
4. Run `python desktop/package_direct.py`. Write down the printed ZIP path. Extract it into a new temporary directory. Do not include a session, credential, export, virtual environment, or old backup in the ZIP.
5. Build a wheel from the extracted folder and check that it contains `signal_archive/direct_app.py`, `direct_service.py`, `direct_export.py`, `telegram_ops.py`, and `autostart.py`. Record the command, exit status, and package filename.

## Step 2 — Prepare each real test machine

1. Use a disposable Telegram account. Do not paste its API Hash, phone, login code, two-step password, session file, message content, or member list into a report.
2. Prepare one supported Linux desktop with a working Secret Service or KDE Wallet keyring and one Windows desktop with Python's `py` launcher and Windows Credential Manager. Use a normal user account, not an administrator shell.
3. Record OS version, Python version, keyring backend name, package ZIP filename and SHA-256 hash. Do not record credentials or exported personal content.
4. Preserve existing installations and data. Extract the ZIP into a new folder. Do not reuse an existing Telegram session.

## Step 3 — Install and authorize, separately on Linux and Windows

1. On Linux, run `sh install-linux.sh`, then `sh start-linux.sh` from the extracted folder. On Windows, run `install-windows.bat`, then `start-windows.bat`.
2. Confirm the installer finishes, then enter Telegram application details and complete the code and optional two-step password prompts locally in the terminal. Never send these values to anyone.
3. Confirm the browser opens a loopback address, the app remains running, and a second launch for the same workspace refuses to use the session concurrently.
4. Close the app and relaunch it. Confirm the existing session is reused and no login code is requested. Record pass/fail and sanitized error text for each OS.

## Step 4 — Verify chat and member exports

1. Prepare a small channel, personal chat, bot conversation and group in the disposable account, each with known test messages. Include a chat in Telegram's archive folder. Include a message with an attachment and a text caption.
2. Refresh dialogs. Confirm search and category filters show each expected source. Save a selection, refresh, and verify the selection persists.
3. Export new messages. Open the export folder and parse each JSONL file. Check the selected chat IDs and known text are present, message IDs are correct, and no media file was downloaded.
4. Add one new test message, export again, and confirm the second export includes the new message without repeating earlier IDs. Confirm an unchanged chat does not produce an empty incremental file.
5. Export a recent period and a full snapshot from More export options. Confirm the recent interval uses message dates, the snapshot includes the current visible history, and a subsequent incremental export does not skip a new message. Check edited and deleted messages using a fresh snapshot; incremental exports do not synchronize old edits or deletions.
6. Use a group's member-export action. Check the CSV contains the members Telegram allows the account to see. Test a group with limited visibility or denied access if available. The interface must not claim the list is complete.
7. Start a longer export, stop it, and check that completed files remain, the current partial file is absent, and a later run does not skip messages. Record observed results, not private content.

## Step 5 — Verify scheduling and recovery

1. Enable a daily time a few minutes ahead and leave the app open. Confirm one export runs and no duplicate starts for the same scheduled day.
2. Enable start-at-sign-in, close the app, sign out and back in. Confirm the app starts and an overdue scheduled export catches up. Disable autostart afterward if it is no longer wanted.
3. If Telegram independently returns a wait, record the duration and observed app behavior without trying to induce or bypass a limit. Confirm the wait survives restart. Unit tests cover simulated waits; they do not prove Telegram server behavior.
4. Close the app. Reinstall over the extracted folder and confirm the previous runtime is saved. In a disposable copy, simulate an installation failure and confirm the old runtime is restored. Keep the real archive and session untouched.
5. On a disposable workspace, run `forget-telegram-login` as documented in the ZIP. Confirm the local session and workspace-specific keyring entry are removed while selections and exports remain. Revoke the disposable authorization in Telegram Settings → Devices separately.

## Step 6 — Resolve failures and decide release readiness

1. For each failed step, record OS, package hash, exact action, sanitized error, expected behavior and actual behavior. Never attach credentials, sessions, chat contents or member CSVs.
2. Reproduce each failure, fix its cause, add an appropriate test, rebuild the ZIP, and repeat the affected steps on the relevant operating system.
3. Re-run the full local checks and both real-machine checklists on the final package. A passing mock test does not replace a real account, browser, keyring, scheduler or Windows test.
4. Only call the application finished after every required behavior above has current evidence on both supported systems and all material failures are resolved. Until then retain the experimental label and describe the remaining limitations accurately.
