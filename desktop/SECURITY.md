# Security notes and release gate

## New direct Telegram workspace

The experimental cross-platform workspace uses a separate Telethon session and does not share the older scheduler's session. On POSIX it sets a restrictive process umask and restricts existing session files and SQLite sidecars to owner access before opening them. On Windows it relies on the user profile's filesystem ACL; POSIX mode bits do not establish Windows ACL protection. Its session is not encrypted by the app. API Hash values use per-workspace OS keyring entries. Local HTTP access is loopback-only and state-changing requests require the local origin and a form token. The direct workspace and its Linux/Windows installers still need live integration testing.

The remaining sections describe the older Linux-only manager and its Telegram Archive service; its paths, locks and uninstall behavior do not apply to the direct workspace.

## Data classification

Signal Archive handles Telegram messages, media, account metadata, API credentials and a Telethon authorization session. Treat all archive data and the session as sensitive personal data.

## Secrets

- API ID is an identifier, not a secret.
- API Hash is stored in the desktop OS keyring using Python `keyring`. Only GNOME Keyring / Secret Service and KDE Wallet backends are accepted. Unknown, null and plaintext backends fail closed.
- The service launcher retrieves API Hash from the keyring and places it in the child process environment only for the lifetime of that process.
- Legacy `.env` API Hash values are migrated to the keyring and removed atomically from `.env`.
- API Hash deletion is verified during uninstall. It does not revoke the app hash at Telegram; The API Hash itself is an application credential; removing its local copy is not the same as revoking the account authorization session.
- The Telethon SQLite session is not encrypted by this application. It is an authorization credential. File permissions and `UMask=0077` reduce local exposure, but full-disk encryption is required for at-rest encryption.

## Local web interface

- Binds only to `127.0.0.1`.
- Requires the expected Host header; POST requests require the exact local Origin.
- POST forms require an unpredictable per-process token.
- Download routes require that token and enforce path containment beneath the export directory.
- This is a single-user local application, not a hardened multi-user web service. Do not expose the port to LAN, reverse proxies or the public internet.

## Export integrity

- Only one export process may hold `.export.lock` (`flock`).
- Each run has a unique identifier and unique temporary filenames.
- Outputs must parse as JSON and contain `chats` and `messages` arrays before being atomically renamed to their final names.
- Per-chat delta checkpoints advance only after the output is validated.
- Export directories are mode `0700`, files `0600`; the manager and generated systemd units use restrictive umasks.
- Recent-period export uses message-date `--from` and `--to` bounds. `--changed-since` remains reserved for archive-record changes in incremental exports.

## Update and rollback

- Telegram Archive, viewer and manager services are stopped before runtime changes.
- SQLite is backed up with the SQLite backup API before migration.
- The pre-update dependency freeze, runtime lock, `.env`, scripts and unit files are retained in a mode-`0700` rollback directory.
- After a successful update, the saved `.env` snapshot has its legacy plaintext API Hash line removed. If the update fails, the rollback copy may retain the legacy Hash so the old service can be restored. Manual rollback to an old service reconstructs the active `.env` from the OS keyring.
- Migrations run on every update.
- A failed update restores the previous scripts, environment, dependency set and SQLite snapshot where available.
- Custom database locations are rejected because this preview's export manager and rollback path target the default SQLite database.

## Uninstall

- Recommended mode keeps only archive database/media and Downloads exports; it removes app code, `.env`, the local Telegram session and API Hash from the OS keyring.
- Full purge also removes archive data, exports and legacy preserved directories.
- Session paths configured outside the user's home directory are refused rather than deleted blindly. The user must inspect them manually.
- Local deletion does not revoke Telegram authorization remotely. The user must terminate the session in Telegram Settings → Devices.
- Secure erasure from SSD remapping, snapshots, backups or filesystem copies cannot be guaranteed.

## Telegram API behavior

Signal Archive's current exports read the local archive database and do not make Telegram API requests. Telegram Archive owns synchronization and Telethon session access. Do not run a second client against the same Telethon session. The systemd archive service uses `Restart=no` to avoid blindly repeating its immediate startup backup after an error. Do not bypass FloodWait, slow mode, hidden-member restrictions or admin permissions. The live dialog discovery and member-export features must be implemented through one coordinated Telegram-session owner before they are enabled.

## Release-blocking integration tests

Before a tester release, verify on a disposable Telegram account and a clean Arch Linux desktop:

1. Fresh install with GNOME Keyring and KDE Wallet separately.
2. Upgrade from 0.3.5 with a real copied SQLite archive and legacy plaintext `.env` API Hash.
3. Migration failure and automatic rollback of database, dependencies, `.env`, scripts and units.
4. `systemd-analyze --user verify` for all generated units, including export timer/service.
5. Reboot with keyring unlocked and locked; verify fail-closed behavior and no plaintext fallback.
6. Two simultaneous export requests from UI and timer; verify only one job executes.
7. Kill during export; verify no partial final file and no checkpoint advancement.
8. Exact recent-period export boundaries, including messages on both ends of the UTC interval.
9. UI uninstall retaining archive data while removing API Hash/session, then full purge including old preserved copies.
10. Confirm remote Telegram session revocation is a separate user action.

Do not describe this preview as production-ready until these integration tests pass.
