"""Streaming exports of participant profiles actually exposed to the signed-in account."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tg_observatory.exceptions import AuthenticationRequired, InvalidRequest
from tg_observatory.exporters.artifact import MarkdownArtifact
from tg_observatory.exporters.markdown import escape
from tg_observatory.services.account_service import credentials_from
from tg_observatory.services.participant_service import _positive_or_signed_id
from tg_observatory.storage.database import Database
from tg_observatory.storage.repositories.accounts import AccountRepository
from tg_observatory.storage.repositories.exports import ExportRepository
from tg_observatory.storage.repositories.participants import ParticipantRepository
from tg_observatory.storage.time import utc_now
from tg_observatory.telegram.client import TelegramGateway

PROFILE_FIELDS = (
    "first_name", "last_name", "username", "phone", "bio", "profile_photo", "bot",
    "premium", "verified", "contact", "public_link", "personal_channel", "saved_music",
)
FIELD_STATES = {"AVAILABLE", "EMPTY", "NOT_EXPOSED", "NOT_FETCHED", "RETRIEVAL_FAILED"}


class MemberExportService:
    def __init__(self, gateway: TelegramGateway, database: Database,
                 accounts: AccountRepository, participants: ParticipantRepository,
                 export_dir: str | Path, *, exports: ExportRepository | None = None,
                 report_message: Callable[[str], None] | None = None,
                 report_progress: Callable[[float, str | None], None] | None = None) -> None:
        self._gateway, self._database = gateway, database
        self._accounts, self._participants = accounts, participants
        self._export_dir, self._exports = export_dir, exports
        self._report = report_message or (lambda _: None)
        self._progress = report_progress or (lambda _value, _message: None)

    async def export(self, job_id: str, params: dict[str, Any]) -> dict[str, Any]:
        dialog_id = _positive_or_signed_id(params.get("dialog_id"))
        source = params.get("source", "telegram")
        include_profiles = params.get("include_profiles", True)
        if not isinstance(include_profiles, bool):
            raise InvalidRequest("include_profiles must be true or false.")
        if source not in {"telegram", "local_snapshot"}:
            raise InvalidRequest("Member export source must be telegram or local_snapshot.")
        if source == "telegram":
            api_id, api_hash = credentials_from(params)
            profile = await self._gateway.account_status(api_id, api_hash)
            if profile is None:
                raise AuthenticationRequired()
            self._accounts.save_profile(profile)
        else:
            profile = self._accounts.get_profile()
            if profile is None:
                raise AuthenticationRequired()
        account_id = self._accounts.local_id(profile.telegram_id)
        dialog = self._database.fetchone(
            "SELECT telegram_id, title, username, kind, participant_count FROM dialogs "
            "WHERE account_id=? AND telegram_id=?", (account_id, dialog_id),
        )
        if dialog is None or dialog["kind"] not in {"group", "channel"}:
            raise InvalidRequest("Select a synced group or channel for member export.")
        extracted_at = utc_now()
        title = f"{dialog['title'] or 'Telegram group'} — Members"
        known = dialog["participant_count"]
        warnings = ["Telegram can restrict participant visibility. Complete enumeration is not verified."]
        metadata = {
            "group_name": dialog["title"], "group_id": dialog_id,
            "group_username": dialog["username"], "extracted_at": extracted_at,
            "source": source, "known_participant_count": known,
            "include_profiles": include_profiles,
            "known_count_source": "last dialog sync; may be stale" if known is not None else "not exposed",
        }
        count, failed_profiles = 0, 0
        with MarkdownArtifact(self._export_dir, job_id, title, "members", metadata,
                              exports=self._exports) as artifact:
            artifact.write_text(
                f"# {escape(dialog['title'])}\n\n## Group metadata\n\n"
                f"- Telegram ID: {dialog_id}\n- Username: {escape(dialog['username'])}\n"
                f"- Extracted at (UTC): {escape(extracted_at)}\n"
                f"- Source: {escape(source)}\n"
                f"- Known participants (last sync): {known if known is not None else 'Not exposed'}\n"
                "- Access: Only participants and fields exposed to this account are included.\n\n## Members\n\n"
            )
            if source == "local_snapshot":
                assert account_id is not None
                snapshot = self._participants.latest(account_id, dialog_id)
                if snapshot["snapshot"] is None:
                    raise InvalidRequest("No completed local participant snapshot is available.")
                warnings.append("Offline snapshot export; profile enrichment was not fetched and data may be stale.")
                artifact.write_text(f"Snapshot captured at: {escape(snapshot['snapshot']['captured_at'])}\n\n")
                for item in snapshot["items"]:
                    fields = {name: {"state": "NOT_FETCHED", "value": None} for name in PROFILE_FIELDS}
                    if item.get("username"):
                        fields["username"] = {"state": "AVAILABLE", "value": item["username"]}
                    if item.get("display_name"):
                        fields["display_name"] = {"state": "AVAILABLE", "value": item["display_name"]}
                    artifact.write_text(_member_section({"telegram_id": item["telegram_id"], "fields": fields}, extracted_at))
                    count += 1
                    await asyncio.sleep(0)
            else:
                try:
                    async for member in self._gateway.iter_member_profiles(
                        api_id, api_hash, dialog_id, include_profiles=include_profiles,
                    ):
                        artifact.write_text(_member_section(member, utc_now()))
                        count += 1
                        failed_profiles += any(field.get("state") == "RETRIEVAL_FAILED"
                                               for field in member["fields"].values())
                        self._report(f"Exported {count} member profiles")
                        self._progress(min(0.99, count / known) if known else 0.0,
                                       f"Exported {count} member profiles")
                        await asyncio.sleep(0)
                except Exception as error:
                    if type(error).__name__ == "FloodWaitError":
                        raise
                    if count == 0:
                        raise InvalidRequest(
                            "No participants could be retrieved. Check this account's member visibility "
                            "permissions and connection, then retry."
                        ) from error
                    warnings.append("Telegram participant retrieval failed or was restricted; only profiles already retrieved are included.")
            if failed_profiles:
                warnings.append(f"Full profile enrichment failed for {failed_profiles} retrieved members.")
            artifact.write_text(
                f"\n## Extraction counts\n\n- Retrieved participants: {count}\n"
                f"- Exported profiles: {count}\n- Profiles with retrieval failures: {failed_profiles}\n"
            )
            result = artifact.complete(count, "partial", warnings)
        self._progress(1.0, "Member export complete (partial visibility)")
        return {**result, "dialog_id": dialog_id, "known_participant_count": known,
                "retrieved_participants": count, "exported_profiles": count, "source": source}


def _member_section(member: dict[str, Any], retrieved_at: str) -> str:
    telegram_id = int(member["telegram_id"])
    fields = member.get("fields", {})
    username = fields.get("username", {}).get("value")
    identity = f"@{username}" if isinstance(username, str) and re.fullmatch(r"[A-Za-z0-9_]{5,32}", username) else f"telegram-user-{telegram_id}"
    lines = [f"## [[{identity}]]", "", f"- Telegram user ID: {telegram_id}",
             f"- Retrieved at (UTC): {escape(retrieved_at)}"]
    for name in (*PROFILE_FIELDS, "display_name"):
        if name not in fields and name == "display_name":
            continue
        field = fields.get(name, {"state": "NOT_EXPOSED", "value": None})
        state = field.get("state", "NOT_EXPOSED")
        if state not in FIELD_STATES:
            state = "NOT_EXPOSED"
        value = field.get("value") if state in {"AVAILABLE", "EMPTY"} else None
        rendered = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list, bool)) else str(value) if value is not None else ""
        lines.append(f"- {name.replace('_', ' ').title()}: {escape(rendered)} ({state})")
    return "\n".join(lines) + "\n\n"
