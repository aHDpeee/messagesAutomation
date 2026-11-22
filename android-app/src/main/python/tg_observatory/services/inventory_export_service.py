"""Selected-dialog Markdown inventory using explicitly available local metadata."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from tg_observatory.exceptions import InvalidRequest
from tg_observatory.exporters.markdown import escape
from tg_observatory.exporters.artifact import MarkdownArtifact
from tg_observatory.storage.repositories.exports import ExportRepository
from tg_observatory.storage.database import Database
from tg_observatory.storage.repositories.accounts import AccountRepository
from tg_observatory.storage.repositories.dialogs import DialogRepository


class InventoryExportService:
    def __init__(self, database: Database, accounts: AccountRepository, dialogs: DialogRepository,
                 export_dir: str | Path, *,
                 exports: ExportRepository | None = None,
                 report_progress: Callable[[float, str | None], None] | None = None) -> None:
        self._database, self._accounts, self._dialogs = database, accounts, dialogs
        self._export_dir = Path(export_dir)
        self._exports = exports or ExportRepository(database)
        self._progress = report_progress or (lambda _value, _message: None)

    async def export(self, job_id: str, params: dict[str, Any],
                     checkpoint: dict[str, Any] | None = None) -> dict[str, Any]:
        selected = params.get("dialog_ids")
        if not isinstance(selected, list) or not selected or len(selected) > 1000:
            raise InvalidRequest("Select between 1 and 1000 dialogs for the inventory.")
        ids: list[int] = []
        for value in selected:
            if isinstance(value, bool) or not isinstance(value, int) or not -(2**63) < value < 2**63 or value == 0:
                raise InvalidRequest("dialog_ids must contain nonzero 64-bit Telegram IDs.")
            if value not in ids:
                ids.append(value)
        categories = params.get("include_categories", ["private", "group", "channel", "bot", "favorite"])
        if not isinstance(categories, list) or not categories or any(
            category not in {"private", "group", "channel", "bot", "favorite", "contact"}
            for category in categories
        ):
            raise InvalidRequest("Choose valid inventory categories.")
        hide_channels = params.get("hide_channels", False)
        override = params.get("include_hidden_channels", False)
        if not isinstance(hide_channels, bool) or not isinstance(override, bool):
            raise InvalidRequest("Channel visibility and its override must be booleans.")
        profile = self._accounts.get_profile()
        account_id = self._accounts.local_id(profile.telegram_id) if profile else None
        if account_id is None:
            raise InvalidRequest("No locally indexed Telegram account is available.")
        # The bounded explicit selection prevents unbounded rows or SQLite parameter counts.
        rows = []
        for dialog_id in ids:
            row = self._database.fetchone(
                "SELECT * FROM dialogs WHERE account_id=? AND telegram_id=?", (account_id, dialog_id),
            )
            if row is None:
                raise InvalidRequest("Every selected dialog must be indexed for this account.")
            if hide_channels and row["kind"] == "channel" and not override:
                raise InvalidRequest("A selected channel is hidden. Explicitly include hidden channels to export it.")
            if row["kind"] in categories or ("favorite" in categories and row["favorite"]):
                rows.append(dict(row))
        if not rows:
            raise InvalidRequest("No selected dialogs match the active categories; contact status is not indexed.")
        warnings = [
            "This inventory contains locally indexed metadata; Telegram metadata may have changed.",
            "Descriptions, contact status, verification and premium flags were not fetched and are marked NOT_FETCHED.",
        ]
        if len(rows) != len(ids):
            warnings.append(f"Active category filters excluded {len(ids) - len(rows)} selected dialogs.")
        with MarkdownArtifact(
            self._export_dir, job_id, "Telegram dialog inventory", "inventory",
            {"aliases": ["Telegram Inventory", "Selected Telegram dialogs"],
             "selected_dialog_ids": ids, "active_categories": categories,
             "include_hidden_channels": override, "source": "local_index"},
            exports=self._exports,
        ) as artifact:
            artifact.write_text("# Telegram dialog inventory\n\n")
            async for chunk in self._render(rows):
                artifact.write_text(chunk)
            return artifact.complete(records=len(rows), completeness="partial", warnings=warnings)

    async def _render(self, rows: list[dict[str, Any]]) -> AsyncIterator[str]:
        yield "## Selected dialogs\n\nUnavailable fields are distinguished from empty values. No authorization material is exported.\n\n"
        for index, row in enumerate(rows):
            yield f"### {escape(row['title'])}\n\n"
            fields = (
                ("Telegram dialog ID", row["telegram_id"]), ("Type", row["kind"]),
                ("Username", row["username"] if row["username"] else "NOT_EXPOSED"),
                ("Description/about", "NOT_FETCHED"),
                ("Member count", row["participant_count"] if row["participant_count"] is not None else "NOT_EXPOSED"),
                ("Latest relevant Telegram activity (UTC)", row["last_message_at"] or "NOT_FETCHED"),
                ("Unread messages", row["unread_count"]), ("Favorite", bool(row["favorite"])),
                ("Archived", bool(row["archived_at"])),
                ("Last opened in Observatory (local provenance)", row["last_opened_at"] or "EMPTY"),
                ("Contact status", "NOT_FETCHED"), ("Verified", "NOT_FETCHED"),
                ("Premium", "NOT_FETCHED"), ("Metadata indexed at", row["updated_at"]),
            )
            for label, value in fields:
                yield f"- {label}: {escape(str(value))}\n"
            username = row["username"]
            if username and re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
                yield f"- Public link: https://t.me/{username}\n"
            else:
                yield "- Public link: NOT_EXPOSED; no private URL was invented.\n"
            yield "\n"
            self._progress((index + 1) / len(rows) * 0.95, f"Rendered {index + 1} dialogs")
            await asyncio.sleep(0)
