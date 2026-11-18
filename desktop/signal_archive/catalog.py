"""Chat metadata, user-defined groups, and the readable CSV catalog.

The CSV is the human-editable settings file: external edits are validated and
adopted, then written back as a canonical snapshot. Dates are always stored as
ISO 8601 in the application state and in the CSV; common spreadsheet date
formats are accepted when the CSV is read. Telegram credentials are never
included.
"""
from __future__ import annotations

import csv
import copy
import io
import hashlib
import json
import os
import re
import tempfile
import uuid
from . import attribution
from datetime import datetime, timedelta, timezone
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


_DATE_FIELDS = ("added_at", "last_message_date", "members_exported_at")

# Spreadsheet applications rewrite ISO timestamps into local display formats.
# Accept the common M/D/Y form (with optional 12/24-hour time) and a year-first
# variant, then store a single canonical ISO value everywhere in the app.
_TIME_PART = r"(?:[ T]+(?P<hour>0?\d|1\d|2[0-3]):(?P<minute>[0-5]\d)(?::(?P<second>[0-5]\d))?(?:\.(?P<fraction>\d{1,6}))?\s*(?P<ampm>[AaPp][Mm])?)?"
_SLASH_DATE = re.compile(r"(?P<month>0?[1-9]|1[0-2])/(?P<day>0?[1-9]|[12]\d|3[01])/(?P<year>\d{2}|\d{4})" + _TIME_PART + r"$")
_DASH_DATE = re.compile(r"(?P<year>\d{4})-(?P<month>0?[1-9]|1[0-2])-(?P<day>0?[1-9]|[12]\d|3[01])" + _TIME_PART + r"$")
# An unformatted spreadsheet cell can also contain the raw Excel serial number.
_EXCEL_SERIAL = re.compile(r"(?P<whole>[1-9]\d{4})(?:\.(?P<fraction>\d+))?$")
_DATE_HINT = "use ISO (2026-11-15T04:15:00+00:00) or a spreadsheet date such as 11/15/26 4:15"


def _date_from_parts(match: re.Match) -> str:
    year = int(match.group("year"))
    if year < 100:
        year += 2000 if year < 69 else 1900
    hour = int(match.group("hour") or 0)
    ampm = match.group("ampm")
    if ampm:
        if not 1 <= hour <= 12:
            raise ValueError("invalid 12-hour clock value")
        hour = hour % 12 + (12 if ampm.lower() == "pm" else 0)
    fraction = match.group("fraction") or ""
    try:
        value = datetime(year, int(match.group("month")), int(match.group("day")), hour,
                         int(match.group("minute") or 0), int(match.group("second") or 0),
                         int(fraction.ljust(6, "0")) if fraction else 0, tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError("date is out of range") from exc
    return value.isoformat()


def _normalize_date(value: str) -> str:
    """Return a canonical ISO date, accepting ISO and spreadsheet formats."""
    text = value.strip()
    if text.startswith("'"):
        text = text[1:].strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).isoformat()
    except ValueError:
        pass
    for pattern in (_SLASH_DATE, _DASH_DATE):
        match = pattern.fullmatch(text)
        if match:
            return _date_from_parts(match)
    match = _EXCEL_SERIAL.fullmatch(text)
    if match:
        number = float(match.group("whole") + "." + (match.group("fraction") or "0"))
        if number < 61:
            raise ValueError("invalid spreadsheet date serial")
        value = datetime(1899, 12, 30, tzinfo=timezone.utc) + timedelta(days=number)
        return value.replace(microsecond=0).isoformat()
    raise ValueError(_DATE_HINT)


def create_block(state: dict, name: str) -> str:
    name = _name(name)
    if name.casefold() == "ungrouped":
        raise ValueError("Ungrouped is reserved for chats exported to the ZIP root.")
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
    if name.casefold() == "ungrouped":
        raise ValueError("Ungrouped is reserved for chats exported to the ZIP root.")
    if any(key != block_id and block["name"].casefold() == name.casefold() for key, block in blocks.items()):
        raise ValueError("A block with this name already exists.")
    blocks[block_id]["name"] = name


def delete_block(state: dict, block_id: str) -> None:
    blocks = state.setdefault("blocks", {})
    if block_id not in blocks:
        raise ValueError("Block not found.")
    removed = {str(i) for i in blocks[block_id].get("chat_ids", [])}
    del blocks[block_id]
    state["selected"] = [i for i in state.get("selected", []) if str(i) not in removed]


def chat_group(state: dict, chat_id: str) -> str | None:
    ident = str(chat_id)
    for block_id, block in state.get("blocks", {}).items():
        if ident in map(str, block.get("chat_ids", [])):
            return str(block_id)
    return "ungrouped" if ident in map(str, state.get("root_selected", [])) else None


def set_chat_group(state: dict, chat_id: str, group_id: str | None) -> None:
    """Assign exactly one export destination; None excludes the chat."""
    ident = str(int(chat_id))
    known = {str(d["id"]) for d in state.get("dialogs", [])} | set(map(str, state.get("selected", [])))
    if ident not in known:
        raise ValueError("Chat is not in the catalog.")
    blocks = state.setdefault("blocks", {})
    if group_id not in (None, "ungrouped") and group_id not in blocks:
        raise ValueError("Group not found.")
    for block in blocks.values():
        block["chat_ids"] = [str(i) for i in block.get("chat_ids", []) if str(i) != ident]
    state["root_selected"] = [str(i) for i in state.get("root_selected", []) if str(i) != ident]
    state["selected"] = [str(i) for i in state.get("selected", []) if str(i) != ident]
    if group_id == "ungrouped":
        state["root_selected"].append(ident)
    elif group_id is not None:
        blocks[group_id].setdefault("chat_ids", []).append(ident)
    if group_id is not None:
        state["selected"].append(ident)


def assign_block(state: dict, block_id: str, chat_ids: list[str]) -> None:
    blocks = state.setdefault("blocks", {})
    if block_id not in blocks:
        raise ValueError("Block not found.")
    known = {str(d["id"]) for d in state.get("dialogs", [])} | set(state.get("selected", []))
    ids = list(dict.fromkeys(str(int(chat_id)) for chat_id in chat_ids))
    if any(chat_id not in known for chat_id in ids):
        raise ValueError("One or more chats are not in the catalog.")
    previous = list(blocks[block_id].get("chat_ids", []))
    for ident in previous:
        if str(ident) not in ids:
            set_chat_group(state, str(ident), None)
    for ident in ids:
        set_chat_group(state, ident, block_id)


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


def _file_hash(path: Path) -> str | None:
    try:
        if path.is_symlink():
            raise ValueError("Catalog must not be a symbolic link.")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except FileNotFoundError:
        return None


def _atomic_write(path: Path, write, *, verify=None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".catalog-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as stream:
            write(stream)
            stream.flush()
            os.fsync(stream.fileno())
        if verify:
            verify()
        os.replace(name, path)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


def write_catalog(state: dict, path: Path, *, expected_hash: str | None = None, importing: bool = False) -> None:
    blocks = state.get("blocks", {})
    selected = set(state.get("selected", []))
    def write(stream):
        writer = csv.DictWriter(stream, fieldnames=CATALOG_FIELDS)
        writer.writeheader()
        for dialog in state.get("dialogs", []):
            ident = str(dialog["id"])
            names = [block["name"] for block in blocks.values() if ident in block.get("chat_ids", [])]
            if state.get("group_semantics_version") == 1:
                names = names[:1] if ident in selected else []
                if ident in selected and not names:
                    names = ["Ungrouped"]
            row = {key: _text(dialog.get(key)) for key in CATALOG_FIELDS}
            block_text = json.dumps(names, ensure_ascii=False) if names and (names[0].startswith("[") or any("," in name for name in names)) else ", ".join(names)
            row.update(id=ident, selected="yes" if ident in selected else "no", link=_text(_link(dialog)), blocks=_text(block_text))
            writer.writerow(row)
    path = Path(path)
    if path.name != "chats.csv":
        _atomic_write(path, write)
        return
    if not importing and (path.parent / "catalog-import.pending.json").exists():
        raise RuntimeError("A catalog import needs recovery. Restart the app before changing the catalog.")
    marker = path.with_name(path.name + ".sha256")
    current = _file_hash(path)
    if importing:
        expected = expected_hash
    else:
        try:
            expected = marker.read_text(encoding="utf-8-sig").strip()
        except FileNotFoundError:
            # Pre-existing catalogs have no marker. Do not risk overwriting an
            # external edit; the user can explicitly reload them first.
            if current is not None:
                raise ValueError("Catalog may have external edits. Reload chats.csv before saving.")
            expected = None
    if current != expected:
        raise ValueError("Catalog changed outside the app. Reload chats.csv before saving.")
    def verify():
        if _file_hash(path) != expected:
            raise ValueError("Catalog changed while saving. Reload chats.csv and try again.")
    _atomic_write(path, write, verify=verify)
    digest = _file_hash(path)
    _atomic_write(marker, lambda stream: stream.write(digest or ""))


def parse_catalog(content: bytes | str, state: dict) -> dict:
    """Validate an edited CSV and return a new state without modifying the original."""
    if isinstance(content, bytes):
        try:
            content = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("Catalog must be UTF-8 CSV.") from exc
    if len(content) > 5_000_000:
        raise ValueError("Catalog is too large (maximum 5 MB).")
    try:
        reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff"), newline=""), strict=True)
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)) or set(reader.fieldnames) != set(CATALOG_FIELDS):
            raise ValueError("Catalog columns do not match the expected format.")
        rows = list(reader)
    except csv.Error as exc:
        raise ValueError("Invalid CSV format.") from exc
    if not rows:
        raise ValueError("Catalog is empty; refusing to erase all chats.")
    old = {str(d["id"]): d for d in state.get("dialogs", [])}
    result = copy.deepcopy(state)
    # Keep block IDs for names that already exist, but let the CSV decide which
    # groups exist and which chats belong to them. A group absent from the CSV
    # is removed; a group whose chats were all removed is no longer stored.
    previous_blocks = {str(b["name"]).casefold(): ident for ident, b in result.get("blocks", {}).items()}
    blocks: dict[str, dict] = {}
    names: dict[str, str] = {}
    dialogs, selected, seen, root_selected = [], [], set(), []
    legacy = state.get("group_semantics_version") != 1

    def unescape(value: str) -> str:
        if value.startswith("'") and (value[1:].lstrip(" \t\r\n").startswith(("=", "+", "-", "@")) or value[1:].startswith(("\t", "\r", "\n"))):
            return value[1:]
        return value

    for row in rows:
        if None in row or any(v is None for v in row.values()):
            raise ValueError("A catalog row has the wrong number of columns.")
        row = {k: unescape(v) for k, v in row.items()}
        ident = row["id"].strip()
        if not re.fullmatch(r"-?[0-9]{1,20}", ident) or int(ident) == 0:
            raise ValueError("Every chat must have a nonzero numeric ID.")
        ident = str(int(ident))
        if ident in seen:
            raise ValueError("Catalog contains duplicate chat IDs.")
        seen.add(ident)
        if row["type"] not in {"chat", "group", "channel", "bot"}:
            raise ValueError("Invalid chat type in catalog.")
        choice = row["selected"].strip().lower()
        if choice not in {"yes", "no", "true", "false", "1", "0"}:
            raise ValueError("Selected must be yes or no.")
        if not row["title"].strip():
            raise ValueError("A chat title cannot be empty.")
        if any(len(v) > 16_384 for v in row.values()):
            raise ValueError("A catalog field is too large.")
        for field in _DATE_FIELDS:
            try:
                row[field] = _normalize_date(row[field])
            except ValueError as exc:
                raise ValueError(f"Invalid date in {field}; {exc}") from exc
        dialog = dict(old.get(ident, {}))
        dialog.update({k: row[k] for k in ("title", "type", "username", "link", "description", "added_via", *_DATE_FIELDS)})
        dialog["id"] = ident
        dialog.setdefault("accessible", True)
        dialogs.append(dialog)
        if legacy and choice in {"yes", "true", "1"}:
            selected.append(ident)
        if row["blocks"].startswith("["):
            try:
                block_values = json.loads(row["blocks"])
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError("Invalid block list in catalog.") from exc
            if not isinstance(block_values, list) or any(not isinstance(x, str) for x in block_values):
                raise ValueError("Invalid block list in catalog.")
        else:
            block_values = row["blocks"].split(",")
        block_values = list(dict.fromkeys(x.strip() for x in block_values if x.strip()))
        if not legacy and len(block_values) > 1:
            raise ValueError("Each chat may have only one group.")
        if not legacy and block_values:
            selected.append(ident)
        for block_name in block_values:
            if not block_name:
                continue
            if not legacy and block_name.casefold() == "ungrouped":
                root_selected.append(ident)
                continue
            block_name = _name(block_name)
            key = block_name.casefold()
            if key not in names:
                ident_block = previous_blocks.get(key) or uuid.uuid4().hex
                blocks[ident_block] = {"name": block_name, "chat_ids": []}
                names[key] = ident_block
            block = blocks[names[key]]
            if ident not in block["chat_ids"]:
                block["chat_ids"].append(ident)
    result["dialogs"], result["selected"], result["blocks"] = dialogs, selected, blocks
    if not legacy:
        result["root_selected"] = root_selected
    return result


def backup_catalog(state: dict, path: Path) -> Path:
    """Save a timestamped private snapshot of the last applied catalog state."""
    path = Path(path)
    folder = path.parent / "catalog-backups"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8] + ".csv"
    backup = folder / name
    write_catalog(state, backup)
    return backup


def backup_external_catalog(path: Path, accepted_backup: Path, *, expected_hash: str | None) -> Path | None:
    """Preserve exact external bytes before replacing a changed local CSV."""
    path = Path(path)
    if expected_hash is None:
        return None
    if path.is_symlink():
        raise ValueError("Catalog must not be a symbolic link.")
    if expected_hash == _file_hash(accepted_backup) and _file_hash(path) == expected_hash:
        return None
    folder = accepted_backup.parent
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8] + ".csv"
    final = folder / name
    fd, temporary = tempfile.mkstemp(prefix=".external-", suffix=".tmp", dir=folder)
    try:
        digest = hashlib.sha256()
        with os.fdopen(fd, "wb") as stream, path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                stream.write(chunk)
                digest.update(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        if digest.hexdigest() != expected_hash:
            raise ValueError("Catalog changed while preparing backup. Reload and try again.")
        os.replace(temporary, final)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return final


def list_catalog_backups(path: Path) -> list[Path]:
    folder = Path(path).parent / "catalog-backups"
    if not folder.is_dir():
        return []
    return sorted((p for p in folder.iterdir() if p.is_file() and not p.is_symlink() and re.fullmatch(r"\d{8}T\d{12}Z-[0-9a-f]{8}\.csv", p.name)), reverse=True)


def apply_catalog(content: bytes | str, state: dict, path: Path) -> tuple[dict, Path]:
    """Validate first, backup applied state, then atomically replace the CSV."""
    expected = _file_hash(Path(path))
    updated = parse_catalog(content, state)
    backup = backup_catalog(state, path)
    backup_external_catalog(path, backup, expected_hash=expected)
    write_catalog(updated, Path(path), expected_hash=expected, importing=True)
    return updated, backup


def restore_catalog(name: str, state: dict, path: Path) -> tuple[dict, Path]:
    matches = {p.name: p for p in list_catalog_backups(path)}
    if name not in matches:
        raise ValueError("Catalog backup not found.")
    return apply_catalog(matches[name].read_bytes(), state, path)


def initialize_catalog(state: dict, path: Path) -> bool:
    """Adopt an old catalog only if it exactly matches the persisted state."""
    path = Path(path)
    if not path.exists():
        write_catalog(state, path)
        return True
    marker = path.with_name(path.name + ".sha256")
    if marker.exists():
        return _file_hash(path) == marker.read_text(encoding="utf-8-sig").strip()
    # Serialize with the same writer to a different basename, without touching
    # the existing user file. Exact equality avoids discarding unknown edits.
    with tempfile.TemporaryDirectory(prefix="catalog-compare-") as folder:
        comparison = Path(folder) / "comparison.csv"
        write_catalog(state, comparison)
        digest = _file_hash(path)
        if digest != _file_hash(comparison):
            return False
        _atomic_write(marker, lambda stream: stream.write(digest or ""))
    return True


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
