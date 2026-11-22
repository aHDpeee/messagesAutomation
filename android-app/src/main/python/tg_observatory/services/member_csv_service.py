"""Export visible Telegram group members as a single streaming CSV."""
from __future__ import annotations

import asyncio
import csv
import os
import tempfile
from pathlib import Path
from typing import Any, Callable

from telethon import errors
from tg_observatory.exceptions import AuthenticationRequired, InvalidRequest, MemberGroupsUnavailable
from tg_observatory.services.account_service import credentials_from
from tg_observatory.services.participant_service import _positive_or_signed_id

FIELDS = ("record_type", "user_id", "username", "first_name", "last_name", "phone", "bio", "bio_status",
          "personal_channel_id", "personal_channel_title", "personal_channel_username", "personal_channel_link", "personal_channel_status", "is_bot",
          "is_premium", "group_role", "note", "source_group_id", "source_group_title", "block group / plan")


def _text(value: Any) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")) else text


def _value(fields: dict, name: str) -> Any:
    field = fields.get(name, {})
    return field.get("value") if field.get("state") in {"AVAILABLE", "EMPTY"} else None


async def export_csv(gateway: Any, database: Any, accounts: Any, directory: Path,
                     job_id: str, params: dict, report: Callable[[str], None]) -> dict:
    if not job_id.isalnum():
        raise InvalidRequest("Invalid export job.")
    groups = params.get("groups")
    include_profiles = params.get("include_profiles", False)
    plan = params.get("plan", "")
    if not isinstance(plan, str) or len(plan) > 200:
        raise InvalidRequest("The plan label must be at most 200 characters.")
    plan = plan.strip()
    if not isinstance(include_profiles, bool):
        raise InvalidRequest("Profile enrichment must be true or false.")
    if not isinstance(groups, list) or not 1 <= len(groups) <= 1000:
        raise InvalidRequest("Choose at least one group.")
    requested = []
    seen = set()
    for entry in groups:
        if not isinstance(entry, dict):
            raise InvalidRequest("Invalid group selection.")
        ident = _positive_or_signed_id(entry.get("id"))
        if ident in seen:
            raise InvalidRequest("Duplicate group.")
        seen.add(ident)
        label = str(entry.get("block", ""))[:200]
        if plan:
            label = f"{label} / {plan}" if label else plan
        requested.append((ident, label))
    api_id, api_hash = credentials_from(params)
    profile = await gateway.account_status(api_id, api_hash)
    if profile is None:
        raise AuthenticationRequired()
    expected_account = str(params.get("expected_account_id") or "")
    if not expected_account.isdigit() or expected_account != str(profile.telegram_id):
        raise InvalidRequest("The Telegram account changed; refresh and verify the catalog before exporting.")
    accounts.save_profile(profile)
    account_id = accounts.local_id(profile.telegram_id)
    resolved = []
    for ident, label in requested:
        dialog = database.fetchone("SELECT title, kind FROM dialogs WHERE account_id=? AND telegram_id=?", (account_id, ident))
        if not dialog or dialog["kind"] != "group":
            raise InvalidRequest("Refresh the dialog list and choose accessible groups.")
        resolved.append((ident, label, dialog["title"] or str(ident)))
    directory = Path(directory) / job_id
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    final = directory / "members.csv"
    fd, temp = tempfile.mkstemp(prefix=".members-", suffix=".part", dir=directory)
    count = 0
    completed_groups = 0
    failed_groups = 0
    completed_ids = []
    failed_ids = []
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            for index, (ident, label, title) in enumerate(resolved):
                if index:
                    await asyncio.sleep(3)
                report(f"Осталось групп: {len(resolved) - index}")
                options = {"include_profiles": include_profiles}
                if include_profiles:
                    options.update(max_profile_details=100, profile_delay=3.0)
                group_position = stream.tell()
                group_start_count = count
                try:
                    async for member in gateway.iter_member_profiles(api_id, api_hash, ident, **options):
                        fields = member.get("fields", {})
                        row = {"record_type": "member", "user_id": member["telegram_id"],
                               "source_group_id": ident, "source_group_title": _text(title),
                               "block group / plan": _text(label), "group_role": "unknown"}
                        for source, destination in (("username", "username"), ("first_name", "first_name"),
                                                    ("last_name", "last_name"), ("phone", "phone"),
                                                    ("bio", "bio"), ("bot", "is_bot"), ("premium", "is_premium")):
                            row[destination] = _text(_value(fields, source))
                        channel = _value(fields, "personal_channel")
                        row["bio_status"] = fields.get("bio", {}).get("state", "NOT_FETCHED")
                        row["personal_channel_status"] = fields.get("personal_channel", {}).get("state", "NOT_FETCHED")
                        if isinstance(channel, dict):
                            row["personal_channel_id"] = _text(channel.get("telegram_channel_id"))
                            row["personal_channel_title"] = _text(channel.get("title"))
                            row["personal_channel_username"] = _text(channel.get("username"))
                            username = channel.get("username")
                            if isinstance(username, str) and username.isascii() and username.replace("_", "").isalnum():
                                row["personal_channel_link"] = f"https://t.me/{username}"
                        writer.writerow(row)
                        count += 1
                        if count % 100 == 0:
                            report(f"Осталось групп: {len(resolved) - index}")
                    completed_groups += 1
                    completed_ids.append(ident)
                except (PermissionError, errors.ChannelPrivateError, errors.ChannelInvalidError,
                        errors.ChatAdminRequiredError, errors.UserNotParticipantError):
                    # Do not publish rows from an incomplete group as if they
                    # were a successful participant list.
                    stream.seek(group_position)
                    stream.truncate()
                    count = group_start_count
                    failed_groups += 1
                    failed_ids.append(ident)
                    report(f"Нет доступа к группе {index + 1}/{len(resolved)}: {title}")
            if not completed_groups:
                raise MemberGroupsUnavailable()
            writer.writerow({"record_type": "project", "note": "Exported with Tegash by ahdpeee and Codex. https://github.com/aHDpeee/messagesAutomation"})
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, final)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise
    return {"file_path": str(final), "members_exported": count, "groups": len(resolved), "failed_groups": failed_groups,
            "completed_group_ids": completed_ids, "failed_group_ids": failed_ids,
            "completeness": "partial", "note": "Only participants visible to the account are included."}
