"""Chat metadata, user-defined blocks, and readable catalog exports.

The state dictionary remains authoritative. CSV is an atomic, readable snapshot;
it is not read back as configuration and never contains Telegram credentials.
"""
from __future__ import annotations

import csv
import os
import re
import tempfile
import uuid
from . import attribution
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any) -> str:
    value = str(value or "")
    if value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")):
        return "'" + value
    return value


def _name(name: str) -> str:
    name = str(name).strip()
    if not name or len(name) > 100 or any(ord(c) < 32 for c in name):
        raise ValueError("Block name must contain 1–100 printable characters.")
    return name


def create_block(state: dict, name: str) -> str:
    name = _name(name)
    blocks = state.setdefault("blocks", {})
    if any(block["name"].casefold() == name.casefold() for block in blocks.values()):
        raise ValueError("A block with this name already exists.")
    ident = uuid.uuid4().hex
    blocks[ident] = {"name": name, "chat_ids": []}
    return ident


def rename_block(state: dict, block_id: str, name: str) -> None:
    blocks = state.setdefault("blocks", {})
    if block_id not in blocks:
        raise ValueError("Block not found.")
    name = _name(name)
    if any(key != block_id and block["name"].casefold() == name.casefold() for key, block in blocks.items()):
        raise ValueError("A block with this name already exists.")
    blocks[block_id]["name"] = name


def delete_block(state: dict, block_id: str) -> None:
    blocks = state.setdefault("blocks", {})
    if block_id not in blocks:
        raise ValueError("Block not found.")
    del blocks[block_id]


def assign_block(state: dict, block_id: str, chat_ids: list[str]) -> None:
    blocks = state.setdefault("blocks", {})
    if block_id not in blocks:
        raise ValueError("Block not found.")
    known = {str(d["id"]) for d in state.get("dialogs", [])} | set(state.get("selected", []))
    ids = list(dict.fromkeys(str(int(chat_id)) for chat_id in chat_ids))
    if any(chat_id not in known for chat_id in ids):
        raise ValueError("One or more chats are not in the catalog.")
    blocks[block_id]["chat_ids"] = ids


def expand_selection(state: dict, chat_ids: list[str], block_ids: list[str]) -> list[str]:
    """Resolve individually checked chats and blocks into stable, deduplicated IDs."""
    blocks = state.get("blocks", {})
    if any(block_id not in blocks for block_id in block_ids):
        raise ValueError("A selected block no longer exists. Refresh the page.")
    all_ids = list(chat_ids)
    for block_id in block_ids:
        all_ids.extend(blocks[block_id].get("chat_ids", []))
    known = {str(d["id"]) for d in state.get("dialogs", [])} | set(state.get("selected", []))
    result = list(dict.fromkeys(str(int(chat_id)) for chat_id in all_ids))
    if any(chat_id not in known for chat_id in result):
        raise ValueError("A selected chat is no longer available. Refresh the page.")
    return result


def merge_dialogs(state: dict, dialogs: list[dict], *, method: str = "telegram refresh", added_at: str | None = None) -> list[dict]:
    """Retain user metadata across refreshes; preserve missing selected sources."""
    previous = {str(d["id"]): d for d in state.get("dialogs", [])}
    now = added_at or _now()
    merged = []
    for dialog in dialogs:
        ident = str(dialog["id"])
        old = previous.get(ident, {})
        record = {**old, **dialog, "id": ident}
        record["added_at"] = old.get("added_at") or now
        record["added_via"] = old.get("added_via") or method
        for key in ("description", "members_exported_at", "last_message_date"):
            if not record.get(key):
                record[key] = old.get(key, "")
        merged.append(record)
    visible = {d["id"] for d in merged}
    retained = set(map(str, state.get("selected", [])))
    for block in state.get("blocks", {}).values():
        retained.update(map(str, block.get("chat_ids", [])))
    for ident in sorted(retained - visible):
        if ident in previous:
            merged.append({**previous[ident], "accessible": False})
    return merged


CATALOG_FIELDS = ("title", "type", "username", "link", "description", "added_via", "added_at", "last_message_date", "members_exported_at", "blocks", "id", "selected")


def _link(dialog: dict) -> str:
    username = str(dialog.get("username") or "")
    if re.fullmatch(r"[A-Za-z0-9_]{1,32}", username):
        return "https://t.me/" + username
    return str(dialog.get("link") or "")


def _atomic_write(path: Path, write) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".catalog-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as stream:
            write(stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


def write_catalog(state: dict, path: Path) -> None:
    blocks = state.get("blocks", {})
    selected = set(state.get("selected", []))
    def write(stream):
        writer = csv.DictWriter(stream, fieldnames=CATALOG_FIELDS)
        writer.writeheader()
        for dialog in state.get("dialogs", []):
            ident = str(dialog["id"])
            names = [block["name"] for block in blocks.values() if ident in block.get("chat_ids", [])]
            row = {key: _text(dialog.get(key)) for key in CATALOG_FIELDS}
            row.update(id=ident, selected="yes" if ident in selected else "no", link=_text(_link(dialog)), blocks=_text(", ".join(names)))
            writer.writerow(row)
    _atomic_write(Path(path), write)


def write_selected_chats(state: dict, path: Path, chat_ids: list[str] | None = None) -> None:
    ids = state.get("selected", []) if chat_ids is None else chat_ids
    known = {str(d["id"]): d for d in state.get("dialogs", [])}
    def write(stream):
        for ident in dict.fromkeys(map(str, ids)):
            dialog = known.get(ident, {})
            title = str(dialog.get("title") or ident).replace("\n", " ").replace("\r", " ")
            stream.write(f"{title} — {ident}")
            link = _link(dialog)
            if link:
                stream.write(f" — {link}")
            stream.write("\n")
        stream.write("\n" + attribution.text() + "\n")
    _atomic_write(Path(path), write)
