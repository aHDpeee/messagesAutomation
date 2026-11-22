"""Deterministic range analytics with disk-backed aggregation and Markdown reporting."""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from tg_observatory.exceptions import InvalidRequest
from tg_observatory.exporters.artifact import MarkdownArtifact
from tg_observatory.exporters.markdown import escape
from tg_observatory.services.export_service import _date_boundary, _message_boundary, _telegram_id
from tg_observatory.storage.database import Database
from tg_observatory.storage.repositories.accounts import AccountRepository
from tg_observatory.storage.repositories.exports import ExportRepository
from tg_observatory.storage.repositories.messages import MessageRepository

_WORD = re.compile(r"\b[^\W\d_][^\W_]*\b", re.UNICODE)
_TAG = re.compile(r"(?<!\w)#([\w]+)", re.UNICODE)
_LINK = re.compile(r"(?:https?://|www\.)[^\s<>]+", re.IGNORECASE)


class AnalyticsExportService:
    def __init__(self, database: Database, accounts: AccountRepository, messages: MessageRepository,
                 export_dir: str | Path, *,
                 exports: ExportRepository | None = None,
                 report_progress: Callable[[float, str | None], None] | None = None) -> None:
        self._database, self._accounts, self._messages = database, accounts, messages
        self._export_dir = Path(export_dir)
        self._exports = exports or ExportRepository(database)
        self._progress = report_progress or (lambda _value, _message: None)

    async def export(self, job_id: str, params: dict[str, Any],
                     checkpoint: dict[str, Any] | None = None) -> dict[str, Any]:
        dialog_id = _telegram_id(params.get("dialog_id"))
        start_id = _message_boundary(params.get("start_message_id"), "start_message_id")
        end_id = _message_boundary(params.get("end_message_id"), "end_message_id")
        start = _date_boundary(params.get("period_start"), "period_start")
        end = _date_boundary(params.get("period_end"), "period_end")
        if (start_id and end_id and start_id > end_id) or (start and end and start >= end):
            raise InvalidRequest("Choose an ordered, nonempty analytics range.")
        stop_words = params.get("stop_words", [])
        if not isinstance(stop_words, list) or len(stop_words) > 10000 or any(
            not isinstance(word, str) or len(word) > 100 for word in stop_words
        ):
            raise InvalidRequest("stop_words must be a list of at most 10000 short strings.")
        stop = {word.casefold() for word in stop_words}
        profile = self._accounts.get_profile()
        account_id = self._accounts.local_id(profile.telegram_id) if profile else None
        dialog = self._database.fetchone(
            "SELECT * FROM dialogs WHERE account_id=? AND telegram_id=?", (account_id, dialog_id),
        )
        if dialog is None:
            raise InvalidRequest("Sync the selected dialog before calculating analytics.")
        self._export_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Histories, vocabularies and interval distributions can exceed RAM: aggregate on disk.
        with tempfile.TemporaryDirectory(prefix=".analytics-", dir=self._export_dir) as directory:
            connection = sqlite3.connect(Path(directory) / "metrics.sqlite")
            connection.row_factory = sqlite3.Row
            try:
                connection.executescript("""
                    PRAGMA temp_store=FILE;
                    PRAGMA cache_size=-2048;
                    CREATE TABLE tokens(kind TEXT, token TEXT, count INTEGER, PRIMARY KEY(kind,token));
                    CREATE TABLE observations(id INTEGER PRIMARY KEY, timestamp TEXT, sender INTEGER,
                        name TEXT, length INTEGER, links INTEGER, media INTEGER, image INTEGER,
                        reply INTEGER, parent INTEGER, text_present INTEGER);
                """)
                count = 0
                after_id = (start_id - 1) if start_id else 0
                while True:
                    page = self._messages.export_page(
                        int(dialog["id"]), after_id=after_id, end_id=end_id,
                        period_start=start, period_end=end,
                    )
                    if not page:
                        break
                    with connection:
                        for message in page:
                            _observe(connection, message, stop)
                    count += len(page)
                    after_id = int(page[-1]["message_id"])
                    self._progress(0.5, f"Analysed {count} indexed messages")
                    await asyncio.sleep(0)
                if count == 0:
                    raise InvalidRequest("The selected analytics range has no indexed messages.")
                connection.executescript("""
                    CREATE INDEX observations_time ON observations(timestamp,id);
                    CREATE TABLE intervals AS
                    SELECT id, timestamp, ROUND(MAX(0,(julianday(timestamp)-julianday(previous))*86400),6) seconds
                    FROM (SELECT id,timestamp,LAG(timestamp) OVER(ORDER BY timestamp,id) previous FROM observations)
                    WHERE previous IS NOT NULL;
                """)
                warnings = [
                    "Metrics describe only locally indexed messages; Telegram retrieval completeness is unverified.",
                    "Unknown senders are excluded from observed-participant counts. No personality or importance is inferred.",
                    "Comment/discussion linkage and a period-matched member denominator are unavailable; those metrics are not reported as zero.",
                ]
                title = f"{dialog['title']} — Conversation analytics"
                metadata = {
                    "aliases": [f"{dialog['title']} Analytics"], "telegram_dialog_id": dialog_id,
                    "period_start": start, "period_end": end, "period_end_semantics": "exclusive",
                    "start_message_id": start_id, "end_message_id": end_id,
                    "message_boundary_semantics": "inclusive", "sample_size": count,
                    "stop_words": sorted(stop), "source": "local_index",
                }
                with MarkdownArtifact(self._export_dir, job_id, title, "analytics", metadata,
                                      exports=self._exports) as artifact:
                    artifact.write_text(f"# {escape(title)}\n\n")
                    self._report(connection, artifact, count, dialog)
                    result = artifact.complete(count, completeness="partial", warnings=warnings)
                    result["dialog_id"] = dialog_id
                    self._progress(1.0, "Analytics report complete")
                    return result
            finally:
                connection.close()

    def _report(self, db: sqlite3.Connection, artifact: MarkdownArtifact, count: int, dialog: Any) -> None:
        totals = db.execute("""SELECT COUNT(DISTINCT sender) senders, SUM(reply) replies,
            SUM(media) media, SUM(image) images, SUM(links) links, SUM(links>0) linked,
            SUM(text_present) text_messages, AVG(length) mean_length, MIN(timestamp) first,
            MAX(timestamp) last FROM observations""").fetchone()
        median_length = _median(db, "observations", "length")
        mean_interval = db.execute("SELECT AVG(seconds) FROM intervals").fetchone()[0]
        median_interval = _median(db, "intervals", "seconds")
        unique_domains = db.execute("SELECT COUNT(*) FROM tokens WHERE kind='domain'").fetchone()[0]
        top = db.execute("SELECT sender,name,COUNT(*) n FROM observations WHERE sender IS NOT NULL GROUP BY sender ORDER BY n DESC,sender LIMIT 10").fetchall()
        top_share = sum(row["n"] for row in top) / count
        concentration = db.execute("SELECT SUM(1.0*n*n)/? FROM (SELECT COUNT(*) n FROM observations WHERE sender IS NOT NULL GROUP BY sender)", (count * count,)).fetchone()[0]
        values = [
            ("Indexed sample", count), ("First / last observed UTC", f"{totals['first']} / {totals['last']}"),
            ("Observed active participants (known sender IDs)", totals["senders"]),
            ("Messages with reply reference", totals["replies"]),
            ("Reply rate (reply-reference messages / sample)", _ratio(totals["replies"], count)),
            ("Channel comments / discussion linkage", "NOT_FETCHED; reply references are not automatically comments"),
            ("Active member ratio", "UNAVAILABLE: no period-matched known-member denominator"),
            ("Shared HTTP(S)/www link occurrences", totals["links"]),
            ("Unique link domains", unique_domains),
            ("Link diversity (unique domains / links)", _ratio(unique_domains, totals["links"])),
            ("Messages containing a link / sample", _ratio(totals["linked"], count)),
            ("Links per message", _ratio(totals["links"], count)),
            ("Image-containing messages / sample", _ratio(totals["images"], count)),
            ("Media-containing messages / sample", _ratio(totals["media"], count)),
            ("Media messages / text-containing messages", _ratio(totals["media"], totals["text_messages"])),
            ("Mean / median text length (Unicode characters)", f"{totals['mean_length']:.2f} / {median_length:.2f}"),
            ("Mean / median consecutive interval (seconds)", f"{_number(mean_interval)} / {_number(median_interval)}"),
            ("Top 10 contributors' share of all messages", f"{top_share:.4f}"),
            ("Observed sender concentration (sum squared message shares)", _number(concentration)),
        ]
        artifact.write_text("## Scope and formulas\n\nUTC throughout; time start inclusive, end exclusive; selected message IDs inclusive. All ratios show their denominator; unavailable denominators produce N/A. Empty-text messages contribute length zero. Words are casefolded Unicode tokens of at least two characters; stop-word matching is exact after casefolding. Hidden text-link targets absent from visible URL text are counted once per message. No default stop words are silently applied.\n\n")
        _table(artifact, ["Metric", "Value"], values)
        self._charts(db, artifact, count)
        artifact.write_text("\n## Top contributors\n\n")
        _table(artifact, ["Sender ID", "Display name", "Messages", "Share of sample"],
               [(row["sender"], row["name"] or "Unknown display name", row["n"], f"{row['n']/count:.4f}") for row in top])
        for kind, label in (("word", "Frequent words"), ("tag", "Hashtags"), ("domain", "Shared domains")):
            artifact.write_text(f"\n## {label}\n\nTop 50; ties ordered lexically.\n\n")
            _table(artifact, ["Value", "Occurrences"], db.execute(
                "SELECT token,count FROM tokens WHERE kind=? ORDER BY count DESC,token LIMIT 50", (kind,),
            ).fetchall())
        depth = db.execute("""WITH RECURSIVE chain(id,parent,depth,path) AS (
            SELECT id,parent,1,','||id||',' FROM observations WHERE parent IS NOT NULL
            UNION ALL SELECT chain.id,o.parent,chain.depth+1,chain.path||o.id||','
            FROM chain JOIN observations o ON o.id=chain.parent
            WHERE o.parent IS NOT NULL AND chain.depth<100 AND instr(chain.path,','||o.id||',')=0
            ) SELECT MAX(depth) FROM chain""").fetchone()[0]
        artifact.write_text(f"\n## Reply chains\n\nMaximum reconstructable reply depth: {depth or 0}. A direct reference counts as depth 1; missing ancestors stop reconstruction, cycles are stopped, and traversal is capped at 100. These are reply relationships, not verified channel discussion comments.\n")

    def _charts(self, db: sqlite3.Connection, artifact: MarkdownArtifact, count: int) -> None:
        for heading, expression in (
            ("Daily activity", "substr(timestamp,1,10)"),
            ("Weekly activity (Monday-based calendar weeks)", "strftime('%Y-W%W',timestamp)"),
            ("Hour-of-day activity (UTC)", "strftime('%H',timestamp)"),
            ("Weekday activity (0 Sunday, 6 Saturday)", "strftime('%w',timestamp)"),
        ):
            artifact.write_text(f"\n## {heading}\n\n")
            # Stream grouped rows; bars use the total sample as a fixed denominator.
            rows = db.execute(f"SELECT {expression} bucket,COUNT(*) n FROM observations GROUP BY bucket ORDER BY bucket")
            artifact.write_text("| Period | Messages | Share (20 blocks = full sample) |\n| --- | ---: | --- |\n")
            for row in rows:
                bar = "█" * round(20 * row["n"] / count) if count >= 2 else "Insufficient sample for chart"
                artifact.write_text(f"| {escape(row['bucket'])} | {row['n']} | {bar} |\n")
        artifact.write_text("\n## Weekday × hour heatmap (UTC)\n\nCounts, with no inferred missing history.\n\n")
        _table(artifact, ["Weekday (0 Sunday)", "Hour", "Messages"], db.execute(
            "SELECT strftime('%w',timestamp),strftime('%H',timestamp),COUNT(*) FROM observations GROUP BY 1,2 ORDER BY 1,2",
        ).fetchall())
        artifact.write_text("\n## Peak one-minute windows / bursts\n\nFixed UTC calendar-minute buckets, top five by count; a burst is not assigned intent.\n\n")
        _table(artifact, ["UTC minute", "Messages"], db.execute(
            "SELECT substr(timestamp,1,16),COUNT(*) n FROM observations GROUP BY 1 ORDER BY n DESC,1 LIMIT 5",
        ).fetchall())
        artifact.write_text("\n## Longest observed inactivity gaps\n\nConsecutive indexed messages only; unindexed history can inflate gaps.\n\n")
        _table(artifact, ["Gap ending UTC", "Seconds"], db.execute(
            "SELECT timestamp,seconds FROM intervals ORDER BY seconds DESC,id LIMIT 5",
        ).fetchall())
        artifact.write_text("\n## Interval distribution\n\n")
        _table(artifact, ["Consecutive interval", "Count"], db.execute("""SELECT CASE
            WHEN seconds<1 THEN '<1 second' WHEN seconds<60 THEN '1–59 seconds'
            WHEN seconds<3600 THEN '1–59 minutes' ELSE 'At least 1 hour' END label,COUNT(*)
            FROM intervals GROUP BY label ORDER BY MIN(seconds)""").fetchall())
        halves = db.execute("""SELECT CASE WHEN julianday(timestamp)<(
            SELECT (MIN(julianday(timestamp))+MAX(julianday(timestamp)))/2 FROM observations)
            THEN 'First temporal half' ELSE 'Second temporal half' END label,COUNT(*)
            FROM observations GROUP BY label ORDER BY label""").fetchall()
        artifact.write_text("\n## Change across equal-duration halves\n\nSplit at the midpoint of first/last observed timestamps. When all timestamps coincide, no temporal comparison is meaningful.\n\n")
        _table(artifact, ["Observed subperiod", "Messages"], halves)


def _observe(db: sqlite3.Connection, message: dict[str, Any], stop: set[str]) -> None:
    text = message.get("text") or ""
    raw = message.get("raw") or {}
    if isinstance(raw, str):
        raw = json.loads(raw)
    visible = [match.group().rstrip(".,;:!?)]}") for match in _LINK.finditer(text)]
    links = list(visible)
    for entity in raw.get("entities", []):
        target = entity.get("url")
        if isinstance(target, str) and target.lower().startswith(("http://", "https://")) and target not in visible:
            links.append(target)
    for kind, tokens in (("word", (word.casefold() for word in _WORD.findall(text))),
                         ("tag", (tag.casefold() for tag in _TAG.findall(text)))):
        for token in tokens:
            if kind == "word" and (len(token) < 2 or token in stop):
                continue
            db.execute("INSERT INTO tokens VALUES(?,?,1) ON CONFLICT(kind,token) DO UPDATE SET count=count+1", (kind, token))
    for link in links:
        try:
            domain = urlsplit(link if "://" in link else "https://" + link).hostname
        except ValueError:
            domain = None
        if domain:
            db.execute("INSERT INTO tokens VALUES('domain',?,1) ON CONFLICT(kind,token) DO UPDATE SET count=count+1", (domain.casefold().rstrip("."),))
    timestamp = datetime.fromisoformat(message["date_utc"]).astimezone(UTC).isoformat(timespec="microseconds")
    db.execute("INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
        message["message_id"], timestamp, message.get("sender_id"),
        message.get("sender_name") or message.get("sender_username"), len(text), len(links),
        int(bool(message.get("media_type"))), int(message.get("media_type") == "photo"),
        int(message.get("reply_to_message_id") is not None), message.get("reply_to_message_id"), int(bool(text)),
    ))


def _median(db: sqlite3.Connection, table: str, column: str) -> float | None:
    count = int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    if not count:
        return None
    result = db.execute(f"SELECT AVG(value) FROM (SELECT {column} value FROM {table} ORDER BY {column} LIMIT ? OFFSET ?)",
                        (2 - count % 2, (count - 1) // 2)).fetchone()[0]
    return float(result)


def _ratio(numerator: int, denominator: int | None) -> str:
    return f"{numerator / denominator:.4f}" if denominator else "N/A"


def _number(value: Any) -> str:
    return f"{float(value):.2f}" if value is not None else "N/A"


def _table(artifact: MarkdownArtifact, headings: list[str], rows: Any) -> None:
    artifact.write_text("| " + " | ".join(headings) + " |\n| " + " | ".join("---" for _ in headings) + " |\n")
    for row in rows:
        artifact.write_text("| " + " | ".join(escape(str(value)) for value in row) + " |\n")
