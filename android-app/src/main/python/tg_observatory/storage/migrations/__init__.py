"""Ordered database migrations. Migration versions are immutable once released."""

from __future__ import annotations

MIGRATIONS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (
        1,
        (
            """CREATE TABLE accounts (
                id INTEGER PRIMARY KEY,
                phone_hint TEXT,
                display_name TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE dialogs (
                id INTEGER PRIMARY KEY,
                account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                telegram_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                kind TEXT NOT NULL,
                archived_at TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(account_id, telegram_id)
            )""",
            """CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                telegram_id INTEGER NOT NULL UNIQUE,
                username TEXT,
                display_name TEXT,
                updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE participants (
                dialog_id INTEGER NOT NULL REFERENCES dialogs(id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                observed_at TEXT NOT NULL,
                PRIMARY KEY(dialog_id, user_id, observed_at)
            )""",
            """CREATE TABLE export_jobs (
                id TEXT PRIMARY KEY,
                account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
                state TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT
            )""",
            "CREATE INDEX idx_dialogs_account_updated ON dialogs(account_id, updated_at)",
            "CREATE INDEX idx_users_username ON users(username)",
            "CREATE INDEX idx_participants_user ON participants(user_id)",
            "CREATE INDEX idx_export_jobs_state ON export_jobs(state)",
        ),
    ),
    (
        2,
        (
            "ALTER TABLE accounts ADD COLUMN telegram_id INTEGER",
            "ALTER TABLE accounts ADD COLUMN username TEXT",
            "ALTER TABLE accounts ADD COLUMN first_name TEXT",
            "ALTER TABLE accounts ADD COLUMN last_name TEXT",
            "ALTER TABLE accounts ADD COLUMN last_sync_at TEXT",
            "CREATE UNIQUE INDEX idx_accounts_telegram_id ON accounts(telegram_id)",
            "ALTER TABLE dialogs ADD COLUMN username TEXT",
            "ALTER TABLE dialogs ADD COLUMN raw_telegram_id INTEGER",
            "ALTER TABLE dialogs ADD COLUMN unread_count INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE dialogs ADD COLUMN participant_count INTEGER",
            "ALTER TABLE dialogs ADD COLUMN last_message_at TEXT",
            "CREATE INDEX idx_dialogs_kind_title ON dialogs(kind, title COLLATE NOCASE)",
            "CREATE INDEX idx_dialogs_last_message ON dialogs(last_message_at)",
            "ALTER TABLE export_jobs ADD COLUMN job_type TEXT NOT NULL DEFAULT 'export'",
            "ALTER TABLE export_jobs ADD COLUMN params_json TEXT NOT NULL DEFAULT '{}'",
            "ALTER TABLE export_jobs ADD COLUMN progress REAL NOT NULL DEFAULT 0",
            "ALTER TABLE export_jobs ADD COLUMN checkpoint_json TEXT",
            "ALTER TABLE export_jobs ADD COLUMN error_json TEXT",
            """CREATE TABLE messages (
                id INTEGER PRIMARY KEY,
                dialog_id INTEGER NOT NULL REFERENCES dialogs(id) ON DELETE CASCADE,
                telegram_message_id INTEGER NOT NULL,
                sender_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                date_utc TEXT NOT NULL,
                text TEXT,
                reply_to_message_id INTEGER,
                edit_date_utc TEXT,
                forward_from_peer_id INTEGER,
                media_type TEXT,
                raw_json TEXT NOT NULL DEFAULT '{}',
                UNIQUE(dialog_id, telegram_message_id)
            )""",
            "CREATE INDEX idx_messages_dialog_date ON messages(dialog_id, date_utc)",
            "CREATE INDEX idx_messages_sender_date ON messages(sender_user_id, date_utc)",
            """CREATE TABLE participant_snapshots (
                id INTEGER PRIMARY KEY,
                dialog_id INTEGER NOT NULL REFERENCES dialogs(id) ON DELETE CASCADE,
                captured_at TEXT NOT NULL,
                participant_count INTEGER NOT NULL,
                UNIQUE(dialog_id, captured_at)
            )""",
            """CREATE TABLE participant_snapshot_members (
                snapshot_id INTEGER NOT NULL REFERENCES participant_snapshots(id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                PRIMARY KEY(snapshot_id, user_id)
            )""",
            "CREATE INDEX idx_participant_snapshots_dialog ON participant_snapshots(dialog_id, captured_at)",
            """CREATE TABLE media (
                id INTEGER PRIMARY KEY,
                message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                mime_type TEXT,
                filename TEXT,
                size_bytes INTEGER,
                local_path TEXT,
                sha256 TEXT,
                download_state TEXT NOT NULL DEFAULT 'pending',
                UNIQUE(message_id, kind, filename)
            )""",
            "CREATE INDEX idx_media_state ON media(download_state)",
            """CREATE TABLE reactions (
                message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                reaction_key TEXT NOT NULL,
                count INTEGER NOT NULL,
                chosen_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(message_id, reaction_key)
            )""",
            """CREATE TABLE export_files (
                id INTEGER PRIMARY KEY,
                export_job_id TEXT NOT NULL REFERENCES export_jobs(id) ON DELETE CASCADE,
                relative_path TEXT NOT NULL,
                size_bytes INTEGER NOT NULL DEFAULT 0,
                sha256 TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(export_job_id, relative_path)
            )""",
            """CREATE TABLE analytics_snapshots (
                id INTEGER PRIMARY KEY,
                scope_type TEXT NOT NULL,
                scope_id INTEGER,
                range_start_utc TEXT,
                range_end_utc TEXT,
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )""",
            "CREATE INDEX idx_analytics_scope_created ON analytics_snapshots(scope_type, scope_id, created_at)",
            """CREATE TABLE sync_state (
                scope_type TEXT NOT NULL,
                scope_id INTEGER NOT NULL,
                cursor TEXT,
                last_sync_at TEXT,
                state_json TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY(scope_type, scope_id)
            )""",
        ),
    ),
    (
        3,
        (
            "ALTER TABLE export_jobs ADD COLUMN started_at TEXT",
            "ALTER TABLE export_jobs ADD COLUMN result_json TEXT",
            "ALTER TABLE export_jobs ADD COLUMN progress_message TEXT",
            "ALTER TABLE export_jobs ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE export_jobs ADD COLUMN resumable INTEGER NOT NULL DEFAULT 0",
            "CREATE INDEX idx_export_jobs_updated ON export_jobs(updated_at DESC)",
        ),
    ),
    (
        4,
        (
            "ALTER TABLE participant_snapshots ADD COLUMN complete INTEGER NOT NULL DEFAULT 1",
            "CREATE INDEX idx_snapshot_members_user ON participant_snapshot_members(user_id, snapshot_id)",
        ),
    ),
    (
        5,
        (
            "ALTER TABLE dialogs ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE dialogs ADD COLUMN last_opened_at TEXT",
            "CREATE INDEX idx_dialogs_recent ON dialogs(account_id, last_opened_at)",
        ),
    ),
    (
        6,
        (
            """CREATE TABLE export_source_messages (
                export_job_id TEXT NOT NULL REFERENCES export_jobs(id) ON DELETE CASCADE,
                telegram_message_id INTEGER NOT NULL,
                PRIMARY KEY(export_job_id, telegram_message_id)
            )""",
        ),
    ),
)
