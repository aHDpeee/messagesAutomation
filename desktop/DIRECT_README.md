# Signal Archive — experimental desktop workspace

This preview exports selected Telegram chats as CSV files inside a ZIP archive, without media. It supports Linux and Windows. Group members visible to your account can be exported to CSV inside a ZIP from the interface. Telegram may hide members or require permissions. Visible phone numbers are included; optionally fetch bios and personal channels for up to 100 profiles per member export, with delays between requests. Live Telegram and real Windows desktop testing have not yet been completed; use a disposable account for initial testing.

## Start

Extract the entire ZIP to a folder you can write to. Python 3.10 or newer and internet access for installation are required.

- Linux: open a terminal in the extracted folder, run `sh start-linux.sh` (it installs dependencies on first launch). A working GNOME Keyring / Secret Service or KDE Wallet is required.
- Windows: install Python 3.10 or newer with the `py` launcher or add Python to PATH, then run `start-windows.bat` (it installs dependencies on first launch). Windows Credential Manager must be available.

To reuse an existing Linux Telegram Archive session, run `sh start-linux.sh --existing-session`. It stops `telegram-archive.service`, checks that the single legacy session file is unused, and starts without requesting a new code. If Telegram has revoked the session, it cannot be reused. Do not start the old archive service while this app uses its session. Sign-in autostart is not supported in this mode; use the same command each time.

To browse and export the existing local archive without any Telegram login or network connection, run `sh start-linux.sh --offline-legacy`. On Windows the equivalent is `start-windows.bat --offline-legacy`; it looks for the old archive at the configured Telegram Archive data location. The old database is opened read-only and exports do not change the old checkpoints. Do not run two instances of this workspace at once.

On first launch, obtain an API ID and API Hash from https://my.telegram.org and follow the prompts for the phone, Telegram code and any two-step verification password. The standalone Windows executable uses browser prompts; the source launchers use terminal prompts. Do not share these credentials. The API Hash is stored in the OS keyring. The Telegram session is stored in the user profile and is not encrypted by this app.

In the local browser interface, refresh the dialog list, use Search with Select visible / Clear visible (or whole groups) to choose chats, save the selection, then export. Selected chats appear first; the per-chat export checkbox is kept in `chats.csv` and is not displayed. Check `In this group` to change group membership. Choose a start date on the main screen, or a date interval under More export options. Each export produces one ZIP in `Downloads/Signal Archive`; open it to find the individual chat CSV files. Existing exports are left untouched. The optional schedule runs while the app is open and catches up on the next launch; use the interface's start-at-sign-in option to start it automatically.

Scheduled exports use the last successful end time for the same selection, or the last 24 hours when there is no previous successful export. Date-based exports do not advance incremental checkpoints. Keep completed ZIP files; retrying an interrupted export can repeat messages, which can be identified by chat ID and message ID.

The chat catalog is `~/.signal-archive/chats.csv` by default (the interface displays the actual path). Edit it in Settings or externally, then use Reload from local CSV; uploaded CSVs and backups can be validated and applied there after confirmation. Existing external edits are preserved and need to be reloaded before exporting.

`chats.csv` is also a settings file you can edit and filter in a spreadsheet. Each row is one chat: `selected` decides whether it is exported, and `blocks` holds its groups (several group names separated by commas, or a JSON list when a name contains a comma). The CSV is authoritative for groups: on reload, groups that no longer appear in it are removed, and a group with no chats is not stored. To filter by group, use your spreadsheet's filter on the `blocks` column, save the CSV, then reload it in Settings.

Dates in `added_at`, `last_message_date` and `members_exported_at` are stored as ISO 8601 (for example `2026-11-15T04:15:00+00:00`). A spreadsheet may display them as `11/15/26 4:15`; that format, `11/15/2026 4:15 PM` and plain spreadsheet date serials are all accepted when the CSV is read, and are normalized to ISO 8601 in the app and in catalogs it writes. Spreadsheet dates carry no time zone, so they are interpreted as UTC.

Close the app before reinstalling. An update preserves the previous virtual environment in a `.venv-direct.saved-*` directory and attempts to restore it if installation fails. To remove the runtime, disable start-at-sign-in first and delete `.venv-direct` and any saved environments. To remove this workspace's local Telegram login while retaining its selections and exports, close the app and run `.venv-direct/bin/python -m signal_archive forget-telegram-login` on Linux, or `.venv-direct\Scripts\python.exe -m signal_archive forget-telegram-login` on Windows. Revoke the old session separately in Telegram Settings → Devices. This command leaves any older preview's shared keyring entry intact.

Telegram controls rate limits and visibility. The app respects FloodWait and does not bypass hidden-member restrictions. Exports reflect messages currently available to the account; incremental exports do not synchronize edits or deletions of earlier messages.

If you launch the app manually during a saved Telegram cooldown, it displays the deadline and exits without connecting. Sign-in autostart waits locally until the deadline and then connects. If autostart was enabled in an older preview, save its setting again to update the startup command.

Message CSV exports include a final project attribution row with `record_type=project`. When importing or analysing message rows, filter for the message record type and exclude this attribution row.

On first start, Signal Archive offers to create a desktop shortcut. You can create it later under **Shortcuts and access**. It also attempts to create a launcher beside the application if that folder is writable; existing shortcuts are never overwritten. A shortcut opens a verified running instance or starts a new one.
