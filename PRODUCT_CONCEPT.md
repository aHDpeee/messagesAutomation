# Product concept: Signal Archive

Signal Archive helps a busy user keep regular local exports of selected Telegram conversations. The user connects their own account, finds channels, groups, private chats and bots in a searchable list, saves a selection, and sees whether the latest synchronization and export succeeded. The application should make the next action clear when login, storage, permissions or a Telegram cooldown prevents completion.

Support Linux first and Windows as well: a local interface, a guided installer, a single owner of the Telegram session, and regular scheduled exports. Exports should be readable outside the application and stored in a predictable folder. Regular exports exclude media. A separate menu action exports group members visible to the account; Telegram may restrict or hide the list, so the app must not imply completeness.

Telegram limits are server decisions, not obstacles to evade. Serialize requests, keep request volume conservative, respect FloodWait, persist the next allowed time, and show the user when work can resume. Do not rotate accounts, hide client identity, bypass access controls, or promise that an account will never be limited.

## Current state

The desktop manager exports data already collected by the upstream Telegram Archive scheduler. Its selection does not control acquisition, and its current list is sourced from the local archive, so a selected dialog cannot be assumed to have been synchronized. Unintegrated scheduler worker code sketches live discovery and member CSV export; it is not installed or exposed by the manager. The Android module is an offline companion. The installer is Linux/systemd-specific and initial login uses a terminal. See desktop/README.md and desktop/SECURITY.md for existing restrictions.

## Confirmed decisions

Linux is the first target, with Windows also required. Exclude media from regular exports. Offer member exports only through the interface menu.
