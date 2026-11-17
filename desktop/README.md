# Signal Archive 0.3.6 — hardening preview

## New cross-platform Telegram workspace (experimental)

This workspace is separate from the older Linux-only manager described below. It uses its own Telegram login session and does not import or modify that manager's archive. It has not yet been tested against a real Telegram account or on Windows; keep the older installation and its backups until those checks have been completed.

- Linux: run `./install-linux.sh`, then `./start-linux.sh` from this folder. Python 3.10+, a working desktop Secret Service or KDE Wallet keyring, and internet access are required.
- Windows: install Python 3.10+ with the `py` launcher or add Python to PATH, run `install-windows.bat`, then `start-windows.bat`. Windows Credential Manager must be available.
- On first launch, obtain an API ID and API Hash from Telegram, enter them and your phone number in the terminal, and follow the Telegram authorization prompts. The API Hash is stored in the OS keyring; the Telegram session is a sensitive local file in `~/.signal-archive` and is not encrypted by the app.
- A workspace is tied to the authorized Telegram account ID to prevent checkpoints being applied to a different account. If an earlier experimental workspace contains selections or checkpoints without an account ID, the app refuses to guess their owner; preserve that directory and use a separate `--home` directory for a new workspace.
- Refresh the dialog list, select chats and save the selection, then export. JSONL files contain messages and identifiers, without media, in `~/Downloads/Signal Archive`. Subsequent exports include new message IDs; they do not synchronize edits or deletions of previously exported messages. Use **More export options → Export full snapshot** for a fresh copy of the currently available history, including current edits. A snapshot does not alter the incremental checkpoint.
- After a successful dialog refresh, the page updates automatically if no selection edits are pending. Save any unsaved selection changes before starting a refresh; if you make changes while a refresh runs, save them to display the updated list.
- **More export options → Export recent messages** exports messages whose Telegram message dates are within the chosen number of days ending when the job starts. This also leaves incremental checkpoints unchanged. Results record the exact UTC interval used.
- When a chat is present in the last refreshed dialog list, message records include its cached title and type alongside its Telegram ID. Titles can change; the ID is the stable identifier within the account.
- Where Telegram includes sender information with a message, records include the cached sender name and username and any post-author signature. These fields can be empty; the app does not make extra per-message requests to look them up.
- JSONL records also include `sender` and `reply_to_message_id` aliases understood by the existing importers. The Android companion has not yet been integration-tested with these exports.
- Regular exports do not create empty files for chats with no new messages; the result list reports those chats as having no new messages. Full snapshots still create a file for an empty chat.
- Long regular exports are saved in segments of up to 1,000 messages per chat and checkpointed after each completed segment. Keep all segments. A shutdown between file creation and checkpoint saving can produce a duplicate segment on the next run; identify duplicates by chat ID and message ID.
- If Telegram denies access to one selected chat, the app continues with the others and reports a partial result with the affected chat. Telegram cooldowns and connection failures still stop the run; already completed files and checkpoints are retained.
- Group-member export is an explicit action next to each group. Telegram may limit or hide the member list, so a completed file is not proof that every member is present. The CSV prefixes an apostrophe to names that spreadsheet programs might treat as formulas; those fields are therefore escaped rather than exact copies.
- An operation can be stopped from the activity area. Completed chat files and their checkpoints are retained; the current partial file is removed. A stopped scheduled export waits until the next scheduled day.
- The daily schedule runs while the app is open and catches up when reopened. The interface offers an opt-in start-at-sign-in setting. The startup command keeps a terminal visible for authorization and error messages. Telegram cooldowns and temporary network failures are retried later; other scheduled failures stop repeated attempts for that day and appear in the activity area.
- Telegram requests are serialized. Message-history, dialog and member pagination use a three-second interval between pages, and selected-chat exports also pause three seconds between chats. Server-requested flood and slow-mode waits are stored and respected after restart. A slow-mode wait pauses the workspace conservatively, even though Telegram may apply it to only one chat. Limits are controlled by Telegram and cannot be predicted or guaranteed.
- A manual launch during a saved cooldown displays the deadline and exits without connecting. Sign-in autostart waits locally until the deadline and then connects, including if Telegram requests a wait during startup. If autostart was enabled in an older preview, save its setting again to install the updated startup command.
- The direct runtime pins Telethon 1.45.0 and keyring 25.7.0. Dialog and member pacing uses Telethon 1.45's iterator behavior; update that dependency only after retesting it.

The install scripts create `.venv-direct` in this folder and do not alter the existing Telegram Archive service. Close Signal Archive before reinstalling or updating. The installer preserves an existing runtime in a uniquely named `.venv-direct.saved-*` directory and attempts to restore it if installation fails. After checking a successful update, you may remove old saved runtimes. To remove this experimental runtime, first disable its start-at-sign-in setting in the interface, then delete that virtual environment. Account data and exports remain in the paths above; removing the local session does not revoke it in Telegram's Devices settings.

To remove the direct workspace's local API Hash and Telegram login while retaining selections, checkpoints and exports, close the app and run `.venv-direct/bin/python -m signal_archive forget-telegram-login` on Linux, or `.venv-direct\Scripts\python.exe -m signal_archive forget-telegram-login` on Windows, from this folder. The next launch asks for credentials again; the retained workspace remains bound to the original Telegram account. This command leaves an older preview's shared keyring entry intact because other workspaces may use it. Revoke the old authorization separately in Telegram Settings → Devices.

Signal Archive is a local-first desktop export workspace built around the existing `telegram-archive` acquisition engine. It does not create a second Telegram client. Telegram Archive remains the owner of the primary Telethon session and full archive synchronization; Signal Archive reads the local SQLite index and exports selected archived chats.

> **Release status:** hardening preview, not yet a tester release candidate. Live discovery of dialogs that have never been archived and group-member export are intentionally not presented as working features. See [Known limitations](#known-limitations).

## Changes in 0.3.6

- Saving the export selection no longer writes `CHAT_IDS` to Telegram Archive's `.env` and no longer restarts the acquisition daemon. Selection now affects exports only.
- `manager.py` is the single export implementation. `telegram_manager.py` is only a compatibility entry point.
- Exports use a cross-process `flock` lock, unique run IDs, temporary output files, JSON validation and atomic finalization. Checkpoints advance only after a valid output is verified.
- Recent-period export uses `export-chat --from --to` with exact UTC timestamps. It no longer misuses `--changed-since`, which represents archive-record changes rather than message dates.
- Export directories are created with mode `0700`; completed JSON files are changed to `0600`.
- API Hash is migrated from legacy `.env` files into the OS keyring. The runtime refuses unknown/plaintext keyring backends rather than silently falling back.
- The uninstaller has explicit modes: retain archive database/media and exports while removing credentials/session, or purge all local data. It also scrubs legacy preserved directories created by earlier installers.
- The manager binds only to `127.0.0.1`, validates `Host` and `Origin` on POST requests, and requires the per-run token for downloaded export files.
- The UI uses a centered phone-width column, search, four category tabs, a More section and progressive list rendering.
- The updater pins Telegram Archive 9.7.0, Uvicorn 0.38.0 and keyring 25.7.0 on first installation, records a runtime lock, stops database/session services before updating, backs up SQLite and dependency state, and runs migrations on every update.
- Initial installation no longer performs a manual backup before starting `schedule`; upstream `schedule` performs the initial backup itself, avoiding the previous duplicate full backup.

## Install / update on Arch Linux

1. Extract the release ZIP.
2. Open its `desktop` folder.
3. Run `update-existing-linux.sh` from a normal desktop terminal. Python 3.14+, a running systemd user manager, and a working desktop keyring (GNOME Keyring / Secret Service or KDE Wallet) are required.
4. On a fresh install, enter API ID, API Hash and phone in the installer. The Hash is entered without terminal echo and stored in the OS keyring. Telegram's login code and optional two-step password are entered only into the upstream CLI prompt.
5. The updater validates the generated systemd units, starts the services and attempts to open `http://127.0.0.1:8001`.

The installer still requires a terminal for the first Telegram authorization. A browser-based login wizard is not part of this preview. If the keyring is locked or unavailable, setup fails closed; it does not write the API Hash to `.env` as a fallback.

## Telegram API fields

- **API ID:** numeric identifier for the Telegram API application. Obtain it from <https://my.telegram.org> → API development tools. It is not your Telegram account ID.
- **API Hash:** 32-character hexadecimal application secret from the same page. Do not share it, place it in screenshots or commit it to source control. It is stored in the desktop OS keyring.
- **Phone:** your Telegram account number in international format, including `+` and country code. It is not a bot token.
- **Login code:** one-time code sent by Telegram during authorization. Enter it only into the local CLI prompt.
- **Two-Step Verification password:** your Telegram account's optional second factor. It is not the API Hash and should never be entered into the web UI.

The API Hash is protected by the desktop keyring, but the Telethon SQLite session remains a bearer credential on disk. This application does **not** encrypt the session database itself. Keep its directory owner-only and use full-disk encryption. A copied session can be as sensitive as an active login.

## Source selection and export

The four tabs currently classify **sources already present in the local archive database**:

- **Recent:** archived sources ordered by the available last-updated field.
- **Channels:** archived channel rows.
- **Groups:** archived group/supergroup rows.
- **Chats/Bots:** other archived dialog rows.

Search, selection persistence, progressive list rendering, and the More disclosure are local operations. Saving the selection does not change Telegram Archive's acquisition whitelist and does not restart its service.

- **Export changes since last export:** uses the upstream `export` command with `--changed-since` and a 10-second overlap. Each chat checkpoint advances only after its output passes JSON validation.
- **Export recent period:** uses exact UTC `--from` and `--to` timestamps and leaves delta checkpoints untouched.
- **Recent exports:** files are stored in `~/Downloads/Signal Archive/`; the UI provides download, open-folder and copy-path actions.

## Schedules and autostart

- Full archive backup: upstream Telegram Archive schedule, default `03:00`.
- Selected-chat delta export: separate Signal Archive timer, default `03:15`.
- Service autostart and browser auto-open are separate settings.

Changing the full-backup time writes the configuration but does not restart the archive service immediately: upstream `schedule` performs an immediate full backup at process startup. The saved schedule takes effect on the next planned service restart. This avoids accidentally launching another immediate full backup from a settings click.

## Uninstall and credential removal

The Settings page contains an uninstall section. The recommended action is **Remove app and credentials, keep archive**. It:

- stops and disables the app's user services;
- removes the API Hash from the OS keyring and verifies the entry is gone;
- removes `.env`, the Telethon session and application code;
- retains the archive database/media and Downloads exports;
- removes legacy preserved `.env` and session copies from previous installer versions.

The destructive option requires typing `DELETE ALL DATA` and removes the archive database, media and exports as well. The CLI is also available:

```bash
./uninstall-linux.sh --keep-archives-remove-credentials
./uninstall-linux.sh --purge
```

Removing local credentials does not revoke a Telegram authorization on Telegram servers. After uninstall, open Telegram Settings → Devices and terminate the old session. SSD snapshots, backups and filesystem-level copies are outside the application's ability to securely erase; use full-disk encryption.

## Known limitations — do not hide these from testers

1. **Unprocessed Telegram dialogs are not discoverable yet.** The source list still comes from the local archive database. The four tabs do not magically include every dialog in the Telegram account.
2. **Group-member export is not implemented.** Telegram may hide member lists or require admin rights. The next implementation must use a single coordinated Telegram-session owner and report `CHAT_ADMIN_REQUIRED`, private chats and hidden-member limitations honestly.
3. **The session database is not application-encrypted.** Only the API Hash uses the desktop keyring. Full-disk encryption is required for stronger at-rest protection.
4. **No persistent Telegram API job queue or progress UI for FloodWait has been added.** Signal Archive's exports are local database reads and do not make Telegram API calls. Upstream Telegram Archive owns Telegram synchronization and its Telethon rate-limit behavior. We do not bypass Telegram rate limits.
5. **Manager HTTP access is loopback-only with origin/host checks and form tokens, but it is not a multi-user authenticated web application.** Do not expose the port to LAN or the public internet.
6. **Android APK and Windows EXE builds are not included or validated here.** The Android companion remains an offline importer/share tool. Windows requires a separate service/scheduler port.
7. **Live Telegram, keyring unlock at boot, real systemd migration, Android and Windows integration have not been tested in this environment.** Back up `~/.local/share/TelegramArchive` before installing. Do not use your primary account as the first integration-test account.

## Verification performed for this preview

- Python syntax compilation for all desktop Python modules.
- Bash syntax checks for installer, uninstaller, rollback and verification scripts.
- Unit tests for import/export core, selection isolation, date-based recent export, checkpoint safety, file permissions, cross-process export locking, and keyring migration/fail-closed behavior.
- Static release-contract checks for duplicate manager logic and forbidden `CHAT_IDS` mutation.

Passing these checks does not establish production readiness. A release candidate still needs a clean Arch install/upgrade/rollback/uninstall test with a disposable Telegram account and a copy of a real database.

## References

- Telegram API credentials: <https://my.telegram.org>
- Telegram `messages.getDialogs`: <https://core.telegram.org/method/messages.getDialogs>
- Telegram `channels.getParticipants`: <https://core.telegram.org/method/channels.getParticipants>
- Telegram API pagination: <https://core.telegram.org/api/offsets>
- Telegram Archive CLI and export date semantics: <https://geiserx.github.io/Telegram-Archive/reference/cli/>
- Telethon sessions: <https://docs.telethon.dev/en/stable/concepts/sessions.html>
- Telethon RPC / FloodWait errors: <https://docs.telethon.dev/en/stable/concepts/errors.html>
- Python keyring: <https://keyring.readthedocs.io/en/stable/>
- systemd unit syntax: <https://www.freedesktop.org/software/systemd/man/latest/systemd.syntax.html>
- Android Sharesheet: <https://developer.android.com/training/sharing/send>
