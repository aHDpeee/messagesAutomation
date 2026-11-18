"""Import Telegram dialog filters without joining chats or changing Telegram folders."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from telethon import functions, types, utils

from . import catalog
from .telegram_ops import _dialog_record, _peer_type, pace_iterator


def _peer_ids(peers: Any) -> set[str]:
    result = set()
    for peer in peers or ():
        try:
            result.add(str(utils.get_peer_id(peer)))
        except (TypeError, ValueError):
            continue
    return result


def _matches(folder: Any, dialog: Any, now: datetime) -> bool | None:
    """Match one filter; None means metadata is insufficient for a safe answer."""
    ident = str(dialog.id)
    included_peers = [*(getattr(folder, "include_peers", ()) or ()), *(getattr(folder, "pinned_peers", ()) or ())]
    excluded_peers = getattr(folder, "exclude_peers", ()) or ()
    is_self = bool(getattr(dialog.entity, "is_self", False))
    included = _peer_ids(included_peers)
    if ident in _peer_ids(excluded_peers) or (is_self and any(isinstance(peer, types.InputPeerSelf) for peer in excluded_peers)):
        return False
    if ident in included or (is_self and any(isinstance(peer, types.InputPeerSelf) for peer in included_peers)):
        return True
    entity = dialog.entity
    kind = _peer_type(entity)
    if kind == "bot":
        matched = bool(getattr(folder, "bots", False))
    elif kind == "group":
        matched = bool(getattr(folder, "groups", False))
    elif kind == "channel":
        matched = bool(getattr(folder, "broadcasts", False))
    elif getattr(entity, "min", False):
        return None if getattr(folder, "contacts", False) or getattr(folder, "non_contacts", False) else False
    else:
        matched = bool(getattr(folder, "contacts" if getattr(entity, "contact", False) else "non_contacts", False))
    if not matched:
        return False
    if getattr(folder, "exclude_archived", False) and getattr(dialog, "folder_id", None) == 1:
        return False
    raw = getattr(dialog, "dialog", None)
    if getattr(folder, "exclude_read", False):
        count = getattr(dialog, "unread_count", None)
        if count is None or raw is None:
            return None
        if not count and not getattr(raw, "unread_mark", False):
            return False
    if getattr(folder, "exclude_muted", False):
        notify = getattr(raw, "notify_settings", None)
        muted_until = getattr(notify, "mute_until", None)
        if muted_until is None:
            return None  # inherited notification settings are not available here
        if isinstance(muted_until, datetime):
            muted = (muted_until if muted_until.tzinfo else muted_until.replace(tzinfo=timezone.utc)) > now
        else:
            muted = int(muted_until) > int(now.timestamp())
        if muted:
            return False
    return True


async def discover(client: Any) -> list[dict[str, Any]]:
    """Fetch folders and a snapshot of matching dialogs using the same client."""
    response = await client(functions.messages.GetDialogFiltersRequest())
    folders = [f for f in getattr(response, "filters", response) if isinstance(f, (types.DialogFilter, types.DialogFilterChatlist))]
    if not folders:
        return []
    output = [{"id": str(f.id), "name": str(getattr(f.title, "text", f.title)), "dialogs": [], "uncertain": 0} for f in folders]
    now = datetime.now(timezone.utc)
    seen_ids: set[str] = set()
    saw_self = False
    async for dialog in pace_iterator(client.iter_dialogs()):
        seen_ids.add(str(dialog.id))
        saw_self |= bool(getattr(dialog.entity, "is_self", False))
        for folder, target in zip(folders, output):
            match = _matches(folder, dialog, now)
            if match is True:
                target["dialogs"].append(_dialog_record(dialog, set()))
            elif match is None:
                target["uncertain"] += 1
    for folder, target in zip(folders, output):
        explicit = [*(getattr(folder, "include_peers", ()) or ()), *(getattr(folder, "pinned_peers", ()) or ())]
        excluded = _peer_ids(getattr(folder, "exclude_peers", ()))
        target["uncertain"] += len((_peer_ids(explicit) - excluded) - seen_ids)
        if not saw_self and any(isinstance(peer, types.InputPeerSelf) for peer in explicit) and not any(
            isinstance(peer, types.InputPeerSelf) for peer in (getattr(folder, "exclude_peers", ()) or ())
        ):
            target["uncertain"] += 1
    return output


def preview(state: dict, folder: dict) -> dict:
    """Describe conflicts and bind the preview to catalog and source snapshot."""
    name = catalog._name(folder["name"])
    if name.casefold() == "ungrouped":
        raise ValueError("Папка Telegram называется Ungrouped; переименуйте её перед импортом.")
    blocks = state.get("blocks", {})
    destination = next((key for key, item in blocks.items() if item["name"].casefold() == name.casefold()), None)
    known = {str(item["id"]): item for item in state.get("dialogs", [])}
    conflicts = []
    for item in folder["dialogs"]:
        ident = str(item["id"])
        group = catalog.chat_group(state, ident) if ident in known else None
        if group is not None and group != destination:
            conflicts.append({"id": ident, "title": str(item.get("title") or ident),
                              "group": "Ungrouped" if group == "ungrouped" else str(blocks[group]["name"])})
    fingerprint = hashlib.sha256(json.dumps({"folder": folder, "blocks": blocks,
        "root_selected": state.get("root_selected", []), "selected": state.get("selected", []),
        "known": sorted(known)}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return {"id": str(folder["id"]), "name": name, "total": len(folder["dialogs"]),
            "uncertain": folder.get("uncertain", 0), "conflicts": conflicts, "fingerprint": fingerprint}


def apply(state: dict, folder: dict, strategy: str, expected: str) -> dict:
    if strategy not in {"keep", "move"}:
        raise ValueError("Choose whether to keep or move conflicting chats.")
    current = preview(state, folder)
    if current["fingerprint"] != expected:
        raise ValueError("The catalog changed since preview. Review the folder again.")
    destination = next((key for key, item in state.get("blocks", {}).items() if item["name"].casefold() == current["name"].casefold()), None)
    if destination is None:
        destination = catalog.create_block(state, current["name"])
    conflict_ids = {item["id"] for item in current["conflicts"]}
    existing = {str(d["id"]) for d in state.get("dialogs", [])}
    added = skipped = 0
    for item in folder["dialogs"]:
        ident = str(item["id"])
        if ident in conflict_ids and strategy == "keep":
            skipped += 1
            continue
        if ident not in existing:
            state["dialogs"] = catalog.merge_dialogs(state, [*state.get("dialogs", []), item], method="Telegram folder")
            existing.add(ident)
        catalog.set_chat_group(state, ident, destination)
        added += 1
    return {"name": current["name"], "added": added, "skipped": skipped, "uncertain": current["uncertain"]}
