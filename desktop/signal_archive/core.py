from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 1

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()

def slug(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return value[:64] or "source"

@dataclass
class Archive:
    home: Path

    @property
    def db_path(self) -> Path:
        return self.home / "archive.sqlite3"

    @property
    def exports_dir(self) -> Path:
        return self.home / "exports"

    def connect(self) -> sqlite3.Connection:
        self.home.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    @contextmanager
    def connection(self):
        con = self.connect()
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def initialize(self) -> None:
        with self.connection() as con:
            con.executescript("""
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sources (
                source_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                source_type TEXT NOT NULL DEFAULT 'telegram',
                external_id TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS collections (
                collection_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS source_collections (
                source_id TEXT NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
                collection_id TEXT NOT NULL REFERENCES collections(collection_id) ON DELETE CASCADE,
                PRIMARY KEY(source_id, collection_id)
            );
            CREATE TABLE IF NOT EXISTS messages (
                record_hash TEXT PRIMARY KEY,
                source_id TEXT NOT NULL REFERENCES sources(source_id),
                external_message_id TEXT,
                date TEXT,
                sender TEXT,
                text TEXT NOT NULL DEFAULT '',
                reply_to TEXT,
                media_json TEXT NOT NULL DEFAULT '[]',
                links_json TEXT NOT NULL DEFAULT '[]',
                raw_json TEXT NOT NULL,
                imported_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_messages_source_date
                ON messages(source_id, date);
            CREATE INDEX IF NOT EXISTS idx_messages_external
                ON messages(source_id, external_message_id);
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                record_hash UNINDEXED, source_id UNINDEXED, sender, text,
                tokenize='unicode61'
            );
            """)
            con.execute(
                "INSERT OR REPLACE INTO metadata(key,value) VALUES('schema_version',?)",
                (str(SCHEMA_VERSION),)
            )

    def add_source(self, source_id: str, title: str, source_type: str = "telegram",
                   external_id: str | None = None) -> None:
        source_id = slug(source_id)
        if not title.strip():
            raise ValueError("Source title cannot be empty")
        with self.connection() as con:
            con.execute("""
                INSERT INTO sources(source_id,title,source_type,external_id,enabled,created_at)
                VALUES(?,?,?,?,1,?)
                ON CONFLICT(source_id) DO UPDATE SET
                    title=excluded.title, source_type=excluded.source_type,
                    external_id=excluded.external_id
            """, (source_id, title.strip(), source_type, external_id, utc_now()))

    def list_sources(self) -> list[dict[str, Any]]:
        with self.connection() as con:
            return [dict(r) for r in con.execute("""
                SELECT s.*, COUNT(m.record_hash) AS message_count,
                       MAX(m.date) AS latest_message
                FROM sources s LEFT JOIN messages m USING(source_id)
                GROUP BY s.source_id ORDER BY s.title COLLATE NOCASE
            """)]

    def add_collection(self, collection_id: str, title: str) -> None:
        collection_id = slug(collection_id)
        if not title.strip():
            raise ValueError("Collection title cannot be empty")
        with self.connection() as con:
            con.execute("""INSERT INTO collections(collection_id,title,created_at)
                VALUES(?,?,?) ON CONFLICT(collection_id) DO UPDATE SET title=excluded.title""",
                (collection_id, title.strip(), utc_now()))

    def assign_source(self, source_id: str, collection_id: str) -> None:
        with self.connection() as con:
            if not con.execute("SELECT 1 FROM sources WHERE source_id=?", (slug(source_id),)).fetchone():
                raise ValueError(f"Unknown source: {source_id}")
            if not con.execute("SELECT 1 FROM collections WHERE collection_id=?", (slug(collection_id),)).fetchone():
                raise ValueError(f"Unknown collection: {collection_id}")
            con.execute("INSERT OR IGNORE INTO source_collections VALUES(?,?)",
                        (slug(source_id), slug(collection_id)))

    def list_collections(self) -> list[dict[str, Any]]:
        with self.connection() as con:
            return [dict(r) for r in con.execute("""
                SELECT c.collection_id,c.title,COUNT(sc.source_id) AS source_count
                FROM collections c LEFT JOIN source_collections sc USING(collection_id)
                GROUP BY c.collection_id ORDER BY c.title COLLATE NOCASE
            """)]

    @staticmethod
    def normalize_record(obj: dict[str, Any], fallback_source: str) -> dict[str, Any]:
        # Supports the project's canonical schema and common Telegram JSON export fields.
        source_id = str(obj.get("source_id") or obj.get("chat_id") or fallback_source)
        msg_id = obj.get("external_message_id", obj.get("message_id", obj.get("id")))
        date = obj.get("date") or obj.get("date_unixtime") or obj.get("timestamp") or ""
        if isinstance(date, (int, float)):
            date = datetime.fromtimestamp(date, timezone.utc).isoformat().replace("+00:00", "Z")
        sender = obj.get("sender") or obj.get("from") or obj.get("from_name") or ""
        if isinstance(sender, dict):
            sender = sender.get("name") or sender.get("id") or canonical_json(sender)
        text = obj.get("text", obj.get("message", ""))
        if isinstance(text, list):
            parts = []
            for part in text:
                if isinstance(part, str):
                    parts.append(part)
                elif isinstance(part, dict):
                    parts.append(str(part.get("text", "")))
            text = "".join(parts)
        elif not isinstance(text, str):
            text = str(text)
        links = obj.get("links") or []
        if not links:
            links = re.findall(r"https?://[^\s<>\"']+", text)
        media = obj.get("media") or obj.get("file") or []
        if isinstance(media, str):
            media = [media]
        elif isinstance(media, dict):
            media = [media]
        reply_to = obj.get("reply_to") or obj.get("reply_to_message_id")
        normalized = {
            "source_id": source_id,
            "external_message_id": str(msg_id) if msg_id is not None else None,
            "date": str(date),
            "sender": str(sender),
            "text": text,
            "reply_to": str(reply_to) if reply_to is not None else None,
            "media": media if isinstance(media, list) else [],
            "links": links if isinstance(links, list) else [],
            "raw": obj,
        }
        normalized["record_hash"] = stable_hash({
            "source_id": source_id,
            "external_message_id": normalized["external_message_id"],
            "date": normalized["date"],
            "sender": normalized["sender"],
            "text": text,
            "reply_to": normalized["reply_to"],
            "media": normalized["media"],
        })
        return normalized

    def import_file(self, path: Path, fallback_source: str) -> dict[str, int]:
        path = path.expanduser().resolve(strict=True)
        if path.stat().st_size > 2 * 1024 * 1024 * 1024:
            raise ValueError("Input exceeds 2 GiB safety limit")
        self.add_source(fallback_source, fallback_source)
        imported = duplicates = invalid = 0
        with path.open("r", encoding="utf-8-sig") as f:
            first = f.read(1)
            f.seek(0)
            if first == "[":
                payload = json.load(f)
                if not isinstance(payload, list):
                    raise ValueError("JSON root must be an array or JSONL records")
                iterator: Iterable[Any] = payload
                for item in iterator:
                    if not isinstance(item, dict):
                        invalid += 1
                        continue
                    if self._insert_normalized(item, fallback_source):
                        imported += 1
                    else:
                        duplicates += 1
            else:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        item = json.loads(line)
                        if not isinstance(item, dict):
                            invalid += 1
                            continue
                        if self._insert_normalized(item, fallback_source):
                            imported += 1
                        else:
                            duplicates += 1
                    except json.JSONDecodeError:
                        invalid += 1
        return {"imported": imported, "duplicates": duplicates, "invalid": invalid}

    def _insert_normalized(self, obj: dict[str, Any], fallback_source: str) -> bool:
        n = self.normalize_record(obj, fallback_source)
        source_id = slug(n["source_id"])
        with self.connection() as con:
            if not con.execute("SELECT 1 FROM sources WHERE source_id=?", (source_id,)).fetchone():
                con.execute("INSERT INTO sources VALUES(?,?,?,?,1,?)",
                            (source_id, source_id, "telegram", None, utc_now()))
            cur = con.execute("""
                INSERT OR IGNORE INTO messages(
                    record_hash,source_id,external_message_id,date,sender,text,
                    reply_to,media_json,links_json,raw_json,imported_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """, (n["record_hash"], source_id, n["external_message_id"], n["date"],
                  n["sender"], n["text"], n["reply_to"], canonical_json(n["media"]),
                  canonical_json(n["links"]), canonical_json(n["raw"]), utc_now()))
            if cur.rowcount:
                con.execute("INSERT INTO messages_fts(record_hash,source_id,sender,text) VALUES(?,?,?,?)",
                            (n["record_hash"], source_id, n["sender"], n["text"]))
                return True
            return False

    def search(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        # FTS5 syntax errors are surfaced clearly; never silently reinterpret user queries.
        with self.connection() as con:
            rows = con.execute("""
                SELECT m.source_id,m.external_message_id,m.date,m.sender,m.text
                FROM messages_fts f
                JOIN messages m ON m.record_hash=f.record_hash
                WHERE messages_fts MATCH ? ORDER BY m.date DESC LIMIT ?
            """, (query, max(1, min(limit, 500)))).fetchall()
            return [dict(r) for r in rows]

    @staticmethod
    def verify_export(output_dir: Path) -> dict[str, Any]:
        output_dir = output_dir.expanduser().resolve()
        manifest_path = output_dir / "manifest.json"
        if not manifest_path.is_file():
            raise FileNotFoundError(f"Missing manifest: {manifest_path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        checked = {}
        for name, metadata in manifest.get("files", {}).items():
            path = output_dir / name
            if not path.is_file():
                checked[name] = {"ok": False, "reason": "missing"}
                continue
            data = path.read_bytes()
            expected_bytes = metadata.get("bytes")
            expected_hash = metadata.get("sha256")
            ok = (expected_bytes is None or len(data) == expected_bytes)
            if expected_hash:
                ok = ok and hashlib.sha256(data).hexdigest() == expected_hash
            checked[name] = {"ok": ok, "bytes": len(data)}
        return {"ok": bool(checked) and all(x["ok"] for x in checked.values()),
                "files": checked}

    def export(self, output_dir: Path, source_ids: list[str] | None = None,
               since: str | None = None, profile: str = "daily-news") -> dict[str, Any]:
        output_dir = output_dir.expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        clauses, args = [], []
        if source_ids:
            clauses.append("source_id IN (" + ",".join("?" for _ in source_ids) + ")")
            args.extend(source_ids)
        if since:
            clauses.append("date >= ?")
            args.append(since)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self.connection() as con:
            rows = con.execute("""
                SELECT source_id,external_message_id,date,sender,text,reply_to,
                       media_json,links_json,raw_json
                FROM messages
            """ + where + " ORDER BY date,source_id,external_message_id", args).fetchall()
            sources = [dict(r) for r in con.execute(
                "SELECT source_id,title,source_type,external_id FROM sources ORDER BY title"
            )]
        records = []
        for r in rows:
            d = dict(r)
            d["media"] = json.loads(d.pop("media_json"))
            d["links"] = json.loads(d.pop("links_json"))
            d["raw"] = json.loads(d.pop("raw_json"))
            records.append(d)
        jsonl_path = output_dir / "messages.jsonl"
        tmp = jsonl_path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8", newline="\n") as f:
            for record in records:
                f.write(canonical_json(record) + "\n")
        tmp.replace(jsonl_path)

        md_path = output_dir / "overview.md"
        md_tmp = md_path.with_suffix(".md.tmp")
        with md_tmp.open("w", encoding="utf-8", newline="\n") as f:
            f.write(f"# Research export: {profile}\n\n")
            f.write(f"- Generated: {utc_now()}\n- Messages: {len(records)}\n")
            f.write(f"- Sources: {len({r['source_id'] for r in records})}\n\n")
            for r in records:
                f.write(f"## {r['date'] or 'Undated'} · {r['source_id']} · {r['sender'] or 'Unknown sender'}\n\n")
                f.write((r["text"] or "[No text]") + "\n\n")
                if r["links"]:
                    f.write("Links: " + ", ".join(map(str, r["links"])) + "\n\n")
                if r["external_message_id"]:
                    f.write(f"Message ID: `{r['external_message_id']}`\n\n")
        md_tmp.replace(md_path)

        digest = hashlib.sha256(jsonl_path.read_bytes()).hexdigest()
        manifest = {
            "format": "signal-archive-export",
            "format_version": 1,
            "application_version": "0.2.0",
            "profile": profile,
            "generated_at": utc_now(),
            "filter": {"source_ids": source_ids or [], "since": since},
            "counts": {"messages": len(records), "sources_in_archive": len(sources),
                       "sources_in_export": len({r["source_id"] for r in records})},
            "files": {
                "messages.jsonl": {"sha256": digest, "bytes": jsonl_path.stat().st_size},
                "overview.md": {"bytes": md_path.stat().st_size}
            },
            "completeness": {
                "database_query_completed": True,
                "telegram_sync_completeness": "unknown",
                "media_download_completeness": "not_verified",
                "external_ai_processing": "not_performed"
            }
        }
        manifest_path = output_dir / "manifest.json"
        mtmp = manifest_path.with_suffix(".json.tmp")
        mtmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        mtmp.replace(manifest_path)
        return manifest
