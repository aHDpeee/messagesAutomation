# Changelog

## 0.3.6 — hardening preview

- Decoupled export selection from Telegram Archive acquisition filters.
- Consolidated export execution behind one manager entry point.
- Added cross-process export locking, unique output names, atomic finalization, JSON validation and restrictive export permissions.
- Corrected recent-period export to use message-date `--from` / `--to` filters.
- Added desktop keyring storage for API Hash, migration from legacy `.env`, fail-closed backend validation and keyring removal during uninstall.
- Reworked uninstall modes to retain only archive database/media and exports by default, or purge all local data explicitly; scrub legacy preserved secret/session copies.
- Added strict loopback Host/Origin checks and token-protected export downloads.
- Added phone-width UI, category tabs, search, More disclosure and progressive list rendering.
- Pinned primary runtime packages, saved a runtime lock, stopped services before update, backed up SQLite/dependency state and migrated on every update.
- Avoided duplicate initial full backup during setup.
- Explicitly documented unimplemented live-dialog discovery, group-member export, session encryption and integration-test limitations.

## 0.3.5

- Previous prototype. Not recommended for tester distribution due to export/acquisition coupling, incomplete uninstall semantics and missing cross-process coordination.
