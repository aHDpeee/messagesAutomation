# Signal Archive — experimental desktop workspace

This preview exports selected Telegram chats as CSV files inside a ZIP archive, without media. It supports Linux and Windows. Group members visible to your account can be exported to CSV inside a ZIP from the interface. Telegram may hide members or require permissions. Visible phone numbers are included; optionally fetch bios and personal channels for up to 100 profiles per member export, with delays between requests. Live Telegram and real Windows desktop testing have not yet been completed; use a disposable account for initial testing.

## Start

Extract the entire ZIP to a folder you can write to. Python 3.10 or newer and internet access for installation are required.

- Linux: open a terminal in the extracted folder, run `sh install-linux.sh`, then `sh start-linux.sh`. A working GNOME Keyring / Secret Service or KDE Wallet is required.
- Windows: install Python 3.10 or newer with the `py` launcher or add Python to PATH, then run `install-windows.bat` and `start-windows.bat`. Windows Credential Manager must be available.

To reuse an existing Linux Telegram Archive session, run `sh start-existing-session-linux.sh` after installation. It stops `telegram-archive.service`, checks that the single legacy session file is unused, and starts without requesting a new code. If Telegram has revoked the session, it cannot be reused. Do not start the old archive service while this app uses its session. Sign-in autostart is not supported in this mode; use the same command each time.

To browse and export the existing local archive without any Telegram login or network connection, run `sh start-old-archive-linux.sh`. On Windows the equivalent is `start-windows.bat --offline-legacy`; it looks for the old archive at the configured Telegram Archive data location. The old database is opened read-only and exports do not change the old checkpoints. Do not run two instances of this workspace at once.

On first launch, obtain an API ID and API Hash from https://my.telegram.org and follow the prompts for the phone, Telegram code and any two-step verification password. The standalone Windows executable uses browser prompts; the source launchers use terminal prompts. Do not share these credentials. The API Hash is stored in the OS keyring. The Telegram session is stored in the user profile and is not encrypted by this app.

In the local browser interface, refresh the dialog list, select chats and save, then export. Regular exports contain new messages only. A recent-period export and full snapshot are available under More export options; they do not change incremental checkpoints. Each export produces one ZIP in `Downloads/Signal Archive`; open it to find the individual chat CSV files. Existing exports are left untouched. The optional schedule runs while the app is open and catches up on the next launch; use the interface's start-at-sign-in option to start it automatically.

Long regular exports are saved as segments of up to 1,000 messages per chat inside the ZIP, with a checkpoint after each completed segment. Keep the ZIP. A sudden shutdown between writing a file and saving its checkpoint can cause that segment to appear again on the next run; use chat ID and message ID to identify duplicates. Recent-period and full-snapshot exports do not advance incremental checkpoints.

Close the app before reinstalling. An update preserves the previous virtual environment in a `.venv-direct.saved-*` directory and attempts to restore it if installation fails. To remove the runtime, disable start-at-sign-in first and delete `.venv-direct` and any saved environments. To remove this workspace's local Telegram login while retaining its selections and exports, close the app and run `.venv-direct/bin/python -m signal_archive forget-telegram-login` on Linux, or `.venv-direct\Scripts\python.exe -m signal_archive forget-telegram-login` on Windows. Revoke the old session separately in Telegram Settings → Devices. This command leaves any older preview's shared keyring entry intact.

Telegram controls rate limits and visibility. The app respects FloodWait and does not bypass hidden-member restrictions. Full snapshots reflect messages currently available to the account; incremental exports do not synchronize edits or deletions of earlier messages.

If you launch the app manually during a saved Telegram cooldown, it displays the deadline and exits without connecting. Sign-in autostart waits locally until the deadline and then connects. If autostart was enabled in an older preview, save its setting again to update the startup command.

Message CSV exports include a final project attribution row with `record_type=project`. When importing or analysing message rows, filter for the message record type and exclude this attribution row.

On first start, Signal Archive offers to create a desktop shortcut. You can create it later under **Shortcuts and access**. It also attempts to create a launcher beside the application if that folder is writable; existing shortcuts are never overwritten. A shortcut opens a verified running instance or starts a new one.
