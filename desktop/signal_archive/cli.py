from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from .core import Archive
from .web import serve

def main():
    parser = argparse.ArgumentParser(prog="signal-archive",
        description="Local-first research archive and structured exporter")
    parser.add_argument("--home", default=str(Path.home() / ".signal-archive"),
                        help="Archive directory")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Initialize local archive")
    add = sub.add_parser("add-source", help="Register a source")
    add.add_argument("source_id"); add.add_argument("title")
    add.add_argument("--type", default="telegram")
    imp = sub.add_parser("import", help="Import JSON array or JSONL")
    imp.add_argument("file"); imp.add_argument("--source", required=True)
    exp = sub.add_parser("export", help="Create JSONL, Markdown and manifest")
    exp.add_argument("--out", required=True); exp.add_argument("--source", action="append")
    exp.add_argument("--since"); exp.add_argument("--profile", default="daily-news")
    search = sub.add_parser("search", help="Search local archive")
    search.add_argument("query")
    sub.add_parser("sources", help="List sources")
    coll = sub.add_parser("add-collection", help="Create a source collection")
    coll.add_argument("collection_id"); coll.add_argument("title")
    assign = sub.add_parser("assign-source", help="Assign a source to a collection")
    assign.add_argument("source_id"); assign.add_argument("collection_id")
    sub.add_parser("collections", help="List collections")
    verify = sub.add_parser("verify", help="Verify an export manifest and file hashes")
    verify.add_argument("directory")
    web = sub.add_parser("serve", help="Open local web workspace")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8765)
    direct = sub.add_parser("telegram", help="Open the Telegram export workspace")
    direct.add_argument("--port", type=int, default=8765)
    direct.add_argument("--exports", default=str(Path.home() / "Downloads" / "Signal Archive"))
    direct.add_argument("--no-browser", action="store_true", help="Do not open a browser at startup")
    direct.add_argument("--wait-for-cooldown", action="store_true", help="Wait locally for a saved Telegram cooldown before connecting")
    direct.add_argument("--session-file", type=Path, help="Use an existing Telethon session file")
    direct.add_argument("--require-existing-auth", action="store_true", help="Never request a new login code")
    direct.add_argument("--offline-legacy", action="store_true", help="Open the old local archive without connecting to Telegram")
    sub.add_parser("forget-telegram-login", help="Remove this workspace's local Telegram login; keep export state and files")
    args = parser.parse_args()
    if args.command == "telegram":
        from .direct_app import run
        try:
            from .shortcut_launcher import stop_existing
            if not args.no_browser:
                stop_existing(Path(args.home).expanduser())
            run(Path(args.home).expanduser(), Path(args.exports).expanduser(), args.port, open_browser=not args.no_browser, wait_for_cooldown=args.wait_for_cooldown, session_file=args.session_file, require_existing_auth=args.require_existing_auth, offline_legacy=args.offline_legacy)
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"Could not start Signal Archive: {exc}", file=sys.stderr)
            raise SystemExit(1) from None
        return
    if args.command == "forget-telegram-login":
        from .direct_app import forget_telegram_login
        try:
            forget_telegram_login(Path(args.home).expanduser())
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"Could not remove local login: {exc}", file=sys.stderr)
            raise SystemExit(1) from None
        print("Local login removed. Existing exports and selection state were kept. Revoke the session separately in Telegram Settings → Devices.")
        return
    archive = Archive(Path(args.home).expanduser())
    if args.command == "init":
        archive.initialize(); print(f"Initialized: {archive.home}")
    elif args.command == "add-source":
        archive.initialize(); archive.add_source(args.source_id,args.title,args.type); print("Source saved")
    elif args.command == "import":
        archive.initialize(); print(json.dumps(archive.import_file(Path(args.file),args.source),indent=2))
    elif args.command == "export":
        archive.initialize()
        result=archive.export(Path(args.out),args.source,args.since,args.profile)
        print(json.dumps(result,indent=2))
    elif args.command == "search":
        archive.initialize()
        for row in archive.search(args.query): print(json.dumps(row,ensure_ascii=False))
    elif args.command == "sources":
        archive.initialize()
        print(json.dumps(archive.list_sources(),indent=2,ensure_ascii=False))
    elif args.command == "add-collection":
        archive.initialize(); archive.add_collection(args.collection_id,args.title); print("Collection saved")
    elif args.command == "assign-source":
        archive.initialize(); archive.assign_source(args.source_id,args.collection_id); print("Source assigned")
    elif args.command == "collections":
        archive.initialize(); print(json.dumps(archive.list_collections(),indent=2,ensure_ascii=False))
    elif args.command == "verify":
        result = Archive.verify_export(Path(args.directory))
        print(json.dumps(result,indent=2))
        if not result["ok"]:
            raise SystemExit(1)
    elif args.command == "serve":
        archive.initialize(); serve(archive,args.host,args.port)

if __name__ == "__main__":
    main()
