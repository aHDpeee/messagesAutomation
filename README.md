# Tegash / Signal Archive

Tegash (formerly Signal Archive) is an experimental local-first Telegram export tool for Linux, Windows and Android. It uses your Telegram account through Telethon to collect text and metadata from selected chats, channels and bots. It does not download media. On desktop, the interface runs in your browser; Android has a native interface.

The project is evolving. Ideas, bug reports, usability feedback and contributions are welcome. If something is confusing or does not work, please open an issue with your operating system, the steps you took and any error message. **Remove chat contents, phone numbers, API credentials, session files and other personal information before sharing logs or screenshots.**

## What it does

- Finds dialogs available to your account and lets you select them for export.
- Exports messages as CSV files grouped in ZIP archives. A scheduled export can run while the application is open.
- Exports visible group members to CSV from the interface. Telegram may restrict which members and profile details are visible.
- Supports local export from the older Telegram Archive database where available.
- Respects Telegram's requested waiting periods rather than trying to bypass them.

The application is a preview, and features and formats may change. Exports are snapshots of content your account can access; incremental exports do not synchronize later message edits or deletions.

## Get started

You need a Telegram account, your own [Telegram API ID and API hash](https://my.telegram.org), and a working OS credential store. Keep API credentials, login codes and session files private.

**Linux:** Install Python 3.10 or newer, then run from the `desktop` folder:

```sh
sh start-linux.sh
```

A Secret Service keyring, such as GNOME Keyring or KDE Wallet, is required. For an existing Linux Telegram Archive session, see [the desktop instructions](desktop/DIRECT_README.md) before using `sh start-linux.sh --existing-session`.

**Windows:** Install Python 3.10 or newer with the `py` launcher or on PATH. Run `desktop\\start-windows.bat`; it installs dependencies on first launch. Windows Credential Manager is required. A standalone, windowed executable can be built on Windows as described in [the Windows build guide](desktop/WINDOWS_BUILD.md); its browser-based first run asks for the API credentials and Telegram login. The user of the resulting executable does not need Python.

Select and save chats in the browser interface before exporting. Exports normally go to `Downloads/Signal Archive`. Keep the application open while an export is running; use the Close button in the interface to exit the windowed executable.

**Android (preview):** See the [Android guide](android-app/README.md) for QR login, device requirements, build and installation. The Android version is under active development and some workflows still need device testing; it does not yet have verified feature parity with desktop.

See [desktop setup and limitations](desktop/DIRECT_README.md) for existing sessions, schedules, recovery and removal of local credentials. The older Linux manager is also present in this workspace; the direct desktop app uses its own session and state unless explicitly started with the old session.

## Development and contributions

Run the local release checks from the repository root:

```sh
SIGNAL_ARCHIVE_PYTHON="$PWD/desktop/.venv-direct/bin/python" sh ./desktop/verify-release.sh
```

Run `python desktop/package_direct.py` to create an allowlisted source ZIP. Building a Windows executable requires a Windows environment; see the build guide. Local tests cannot by themselves establish that Telegram authorization or a Windows desktop works on a particular machine.

Ideas, fixes, documentation, translations and testing across platforms are all welcome. For a change, describe the problem and include relevant checks; never include personal Telegram data or credentials. See the [project license](LICENSE) for non-commercial use and redistribution terms and its scope; the existing desktop component remains MIT-licensed under [desktop/LICENSE](desktop/LICENSE). Third-party dependencies retain their own licenses.

Android development priorities and remaining checks are in the [finalization plan](ANDROID_FINALIZATION_PLAN.md). Feedback and contributions are welcome through the [GitHub repository](https://github.com/aHDpeee/messagesAutomation); contact the author on [Telegram](https://t.me/ahdpeee).

See [release notes and platform tags](RELEASES.md) and [the releases page](https://github.com/aHDpeee/messagesAutomation/releases) for available previews and update behavior.

Signal Archive is developed by ahdpeee with help from Codex.

Message CSV exports include a final project attribution row with `record_type=project`. When importing or analysing message rows, filter for the message record type and exclude this attribution row.

On first start, Signal Archive offers to create a desktop shortcut. You can create it later under **Shortcuts and access**. It also attempts to create a launcher beside the application if that folder is writable; existing shortcuts are never overwritten. A shortcut opens a verified running instance or starts a new one.
