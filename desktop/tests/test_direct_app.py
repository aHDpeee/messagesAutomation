import zipfile
import csv
import io
import asyncio
import importlib.util
import json
import os
import sqlite3
from concurrent.futures import Future
import tempfile
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from unittest.mock import AsyncMock, patch

from signal_archive.direct_app import Runtime, SessionLock, _connect_client, _disconnect_client, _secret_account, credentials, forget_telegram_login, make_handler, protect_existing_session, run, scheduler, wait_for_saved_cooldown, parse_form_datetime
from signal_archive.direct_service import DirectService, selection_fingerprint
from signal_archive import legacy_archive, catalog


class ClientTimeTests(unittest.TestCase):
    def test_browser_instant_preserves_timezone_independent_of_server(self):
        parsed = parse_form_datetime({"start": ["2026-03-29T03:30"], "start_instant": ["2026-03-29T01:30:00.000Z"]}, "start")
        self.assertEqual(parsed.isoformat(), "2026-03-29T01:30:00+00:00")

    def test_instant_requires_timezone(self):
        with self.assertRaisesRegex(ValueError, "timezone"):
            parse_form_datetime({"start": ["2026-01-01T10:00"], "start_instant": ["2026-01-01T10:00"]}, "start")


class FakeRuntime:
    def __init__(self, service):
        self.service = service
        self.submitted = []
        self.shutdown_called = False
    def call(self, operation):
        return operation()
    def submit(self, operation):
        self.submitted.append(operation)
    def shutdown(self):
        self.shutdown_called = True


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.legacy_patch = patch.object(legacy_archive, "DEFAULT_DB", root / "missing-legacy.sqlite")
        self.legacy_patch.start()
        self.addCleanup(self.legacy_patch.stop)
        self.service = DirectService(None, root / "state", root / "exports")
        self.service.state["dialogs"] = [{"id": "-1001", "title": "Group & friends", "type": "group"},
                                         {"id": "12", "title": "A channel", "type": "channel"}]
        self.runtime = FakeRuntime(self.service)
        self.handler_type = make_handler(self.runtime, "test-token")

    def tearDown(self):
        self.temp.cleanup()

    def request(self, method, path, body="", headers=None):
        handler = self.handler_type.__new__(self.handler_type)
        handler.path = path
        handler.server = types.SimpleNamespace(server_port=8765)
        handler.headers = {"Host": "127.0.0.1:8765", "Content-Length": str(len(body.encode())), **(headers or {})}
        handler.rfile = io.BytesIO(body.encode())
        handler.wfile = io.BytesIO()
        handler.request_version = "HTTP/1.1"
        handler.requestline = method + " " + path + " HTTP/1.1"
        getattr(handler, "do_" + method)()
        return handler.wfile.getvalue().decode()

    def test_page_and_selection(self):
        page = self.request("GET", "/")
        self.assertIn("200 OK", page)
        self.assertIn("Group &amp; friends", page)
        self.assertIn("Export members", page)
        self.assertIn("action=/members>", page)
        self.assertIn("name=enrich_profiles value=yes", page)
        for endpoint in ("export", "interval"):
            self.assertIn(f"action=/{endpoint} onsubmit='return ensureSelectionSaved()'", page)
        self.assertEqual(page.count("name=selection value="), 3)
        self.assertNotIn("Export full snapshot", page)
        form = urlencode({"token": "test-token", "chat": "-1001", "selection": selection_fingerprint([])})
        response = self.request("POST", "/select", form, {"Origin": "http://127.0.0.1:8765"})
        self.assertIn("303 See Other", response)
        self.assertEqual(self.service.state["selected"], ["-1001"])

    def test_group_tabs_sort_selected_and_single_chat_preferences_persist(self):
        ident = catalog.create_block(self.service.state, "Work")
        catalog.assign_block(self.service.state, ident, ["12"])
        self.service.state["selected"] = ["12"]
        page = self.request("GET", "/")
        self.assertIn("aria-label='Chat groups'", page)
        self.assertIn("Ungrouped", page)
        self.assertIn("data-group='" + ident + "'", page)
        self.assertIn("<dialog id=single-dialog", page)
        self.assertLess(page.index("data-type='channel'"), page.index("data-type='group'"))
        form = urlencode({"token": "test-token", "chat_id": "12", "start": "2026-11-01T09:00", "end": "2026-11-02T10:00"})
        response = self.request("POST", "/single-export", form, {"Origin": "http://127.0.0.1:8765"})
        self.assertIn("303 See Other", response)
        self.assertEqual(self.service.state["single_export_settings"]["12"], {"start": "2026-11-01T09:00", "end": "2026-11-02T10:00"})
        page = self.request("GET", "/")
        self.assertIn("data-start='2026-11-01T09:00'", page)
        self.assertIn("data-end='2026-11-02T10:00'", page)

    def test_group_membership_is_separate_from_export_selection(self):
        group = catalog.create_block(self.service.state, "Work")
        self.service.state["selected"] = ["12"]
        catalog.write_catalog(self.service.state, self.service.catalog_path)
        expected = self.service.catalog_content()[1]
        origin = {"Origin": "http://127.0.0.1:8765"}
        form = urlencode({"token": "test-token", "chat_id": "-1001", "group": group, "checked": "yes", "expected_hash": expected})
        reply = self.request("POST", "/group-membership", form, origin)
        self.assertIn("200 OK", reply)
        response = json.loads(reply.split("\r\n\r\n", 1)[1])
        self.assertEqual(response["groups"], [group])
        self.assertEqual(self.service.state["selected"], ["12"])
        self.assertIn("-1001", self.service.state["blocks"][group]["chat_ids"])
        stale = self.request("POST", "/group-membership", form, origin)
        self.assertIn("400 Bad Request", stale)
        ungroup = urlencode({"token": "test-token", "chat_id": "-1001", "group": "ungrouped", "checked": "yes", "expected_hash": response["hash"]})
        reply = self.request("POST", "/group-membership", ungroup, origin)
        self.assertIn("200 OK", reply)
        self.assertEqual(self.service.state["blocks"][group]["chat_ids"], [])
        self.assertEqual(self.service.state["selected"], ["12"])

    def test_ungrouped_cannot_be_unchecked_without_a_target_group(self):
        expected = self.service.catalog_content()[1]
        form = urlencode({"token": "test-token", "chat_id": "12", "group": "ungrouped", "checked": "no", "expected_hash": expected})
        reply = self.request("POST", "/group-membership", form, {"Origin": "http://127.0.0.1:8765"})
        self.assertIn("400 Bad Request", reply)
        self.assertIn("Choose a named group", reply)

    def test_catalog_controls_require_preview_and_show_path(self):
        page = self.request("GET", "/")
        self.assertIn(str(self.service.home / "chats.csv"), page)
        for operation in ("upload", "reload", "restore"):
            self.assertIn(f"previewCatalog(this,'{operation}')", page)
        self.assertNotIn("id=catalog-editor", page)
        self.assertIn("data-page=settings hidden", page)

    def test_external_catalog_edit_shows_conflict_notice(self):
        self.service.catalog_path.write_text("externally changed", encoding="utf-8")
        page = self.request("GET", "/")
        self.assertIn("Cannot apply changes from chats.csv", page)
        self.assertIn("The file was kept unchanged", page)

    def test_opening_page_applies_valid_external_catalog_edit(self):
        catalog.write_catalog(self.service.state, self.service.catalog_path)
        original = self.service.catalog_path.read_text(encoding="utf-8-sig")
        modified = original.replace("Group & friends", "Renamed group")
        self.service.catalog_path.write_text(modified, encoding="utf-8-sig")
        page = self.request("GET", "/")
        self.assertIn("Renamed group", page)
        self.assertEqual(self.service.state["dialogs"][0]["title"], "Renamed group")
        self.assertEqual(self.service.catalog_path.read_text(encoding="utf-8-sig"), modified)

    def test_catalog_preview_save_reload_upload_and_restore(self):
        catalog.write_catalog(self.service.state, self.service.catalog_path)
        original, original_hash = self.service.catalog_content()
        origin = {"Origin": "http://127.0.0.1:8765"}
        invalid = urlencode({"token": "test-token", "operation": "edit", "csv": "wrong,columns\n1,2"})
        self.assertIn("400 Bad Request", self.request("POST", "/catalog-preview", invalid, origin))
        self.assertEqual(self.service.catalog_content()[0], original)

        edited = original.replace("Group & friends", "Work group")
        preview = urlencode({"token": "test-token", "operation": "edit", "csv": edited})
        reply = self.request("POST", "/catalog-preview", preview, origin)
        self.assertIn("200 OK", reply)
        self.assertEqual(json.loads(reply.split("\r\n\r\n", 1)[1])["hash"], original_hash)
        save = urlencode({"token": "test-token", "csv": edited, "expected_hash": original_hash})
        self.assertIn("303 See Other", self.request("POST", "/catalog-edit", save, origin))
        self.assertEqual(self.service.state["dialogs"][0]["title"], "Work group")
        backups = self.service.catalog_backups()
        self.assertTrue(backups)

        current, current_hash = self.service.catalog_content()
        raced = current.replace("Work group", "External edit")
        self.service.catalog_path.write_text(raced, encoding="utf-8")
        stale = urlencode({"token": "test-token", "csv": current, "expected_hash": current_hash})
        self.assertIn("400", self.request("POST", "/catalog-upload", stale, origin))
        self.assertIn("External edit", self.service.catalog_path.read_text(encoding="utf-8"))
        _, external_hash = self.service.catalog_content()
        reload_form = urlencode({"token": "test-token", "expected_hash": external_hash})
        self.assertIn("303 See Other", self.request("POST", "/catalog-reload", reload_form, origin))
        self.assertEqual(self.service.state["dialogs"][0]["title"], "External edit")

        _, current_hash = self.service.catalog_content()
        upload = urlencode({"token": "test-token", "csv": edited, "expected_hash": current_hash})
        self.assertIn("303 See Other", self.request("POST", "/catalog-upload", upload, origin))
        _, current_hash = self.service.catalog_content()
        restore_preview = urlencode({"token": "test-token", "operation": "restore", "name": backups[0]})
        reply = self.request("POST", "/catalog-preview", restore_preview, origin)
        self.assertIn("200 OK", reply)
        restore = urlencode({"token": "test-token", "name": backups[0], "expected_hash": current_hash})
        self.assertIn("303 See Other", self.request("POST", "/catalog-restore", restore, origin))
        self.assertEqual(self.service.state["dialogs"][0]["title"], "Group & friends")

    def test_remote_gateway_page_does_not_disclose_pairing_controls_or_codes(self):
        gate = types.SimpleNamespace(pending=lambda: [{"id": "abc", "device_name": "Phone", "approved": False, "code": "123456"}])
        self.runtime.lan_gateway = types.SimpleNamespace(gate=gate, origin="https://host.test:8443", store=types.SimpleNamespace(records={}))
        local = self.request("GET", "/")
        remote = self.request("GET", "/", headers={"X-Signal-Remote": "1"})
        self.assertIn("123456", local)
        self.assertNotIn("123456", remote)
        self.assertNotIn("LAN access and paired devices", remote)

    def test_shortcut_offer_is_remembered_and_settings_remain(self):
        self.assertIn('Add Signal Archive to your desktop', self.request('GET', '/'))
        form = urlencode({'token': 'test-token', 'choice': 'no'})
        self.assertIn('303 See Other', self.request('POST', '/shortcut-offer', form, {'Origin': 'http://127.0.0.1:8765'}))
        page = self.request('GET', '/')
        self.assertNotIn('Add Signal Archive to your desktop', page)
        self.assertIn('Create desktop shortcut', page)

    def test_shortcut_offer_yes_creates_desktop_link(self):
        form = urlencode({'token': 'test-token', 'choice': 'yes'})
        with patch('signal_archive.shortcuts.create_shortcut') as create, \
             patch('signal_archive.shortcuts.desktop_directory', return_value=Path('/desktop')):
            self.assertIn('303 See Other', self.request('POST', '/shortcut-offer', form, {'Origin': 'http://127.0.0.1:8765'}))
        create.assert_called_once()
        self.assertTrue(self.service.state['shortcut_offer_answered'])

    def test_member_profile_option_reaches_service(self):
        form = urlencode({"token": "test-token", "chat_id": "-1001", "enrich_profiles": "yes"})
        response = self.request("POST", "/members", form, {"Origin": "http://127.0.0.1:8765"})
        self.assertIn("303 See Other", response)
        with patch.object(self.service, "export_members", new_callable=AsyncMock) as export:
            asyncio.run(self.runtime.submitted[-1]())
        export.assert_awaited_once_with("-1001", enrich_profiles=True, password=None)

    def test_problem_tab_appears_only_with_problems_and_can_be_resolved(self):
        from signal_archive import problem_chats
        recent = datetime.now(timezone.utc).isoformat()
        for dialog in self.service.state["dialogs"]:
            dialog["last_message_date"] = recent
        page = self.request("GET", "/")
        self.assertNotIn("Проблемные чаты (", page)
        self.assertIn("action=/problem-settings", page)
        self.assertIn("value='30'", page)
        self.assertIn("action=/switch-session", page)
        self.assertIn("action=/members-batch", page)
        self.assertIn("signal-add-alert", page)

        problem_chats.record_problem(self.service.state, "-300", title="Pending & group",
                                     kind="group", reason="requested", message="Ожидает одобрения <админом>")
        page = self.request("GET", "/")
        self.assertIn("Проблемные чаты (1)", page)
        self.assertIn("class=\"page-tab problem-tab\"", page)
        self.assertIn("Pending &amp; group", page)
        self.assertIn("Ожидает одобрения &lt;админом&gt;", page)
        self.assertIn("name=problem_chat value=\"-300\"", page)
        form = urlencode({"token": "test-token", "chat_id": "-300"})
        self.assertIn("303 See Other", self.request("POST", "/problem-resolve", form,
                                                    {"Origin": "http://127.0.0.1:8765"}))
        self.assertNotIn("Проблемные чаты (", self.request("GET", "/"))

    def test_problem_settings_batch_scope_and_session_switch(self):
        from signal_archive import session as session_ops
        origin = {"Origin": "http://127.0.0.1:8765"}
        form = urlencode({"token": "test-token", "days": "7"})
        self.assertIn("303 See Other", self.request("POST", "/problem-settings", form, origin))
        self.assertEqual(self.service.state["problem_chat_days"], 7)
        self.assertIn("value='7'", self.request("GET", "/"))
        invalid = urlencode({"token": "test-token", "days": "0"})
        self.assertIn("400 Bad Request", self.request("POST", "/problem-settings", invalid, origin))

        form = urlencode({"token": "test-token", "scope": "all_groups", "plan": "July",
                          "enrich_profiles": "yes"})
        self.assertIn("303 See Other", self.request("POST", "/members-batch", form, origin))
        self.assertTrue(self.runtime.submitted)
        with patch.object(self.service, "export_members_batch", new_callable=AsyncMock) as export:
            asyncio.run(self.runtime.submitted[-1]())
        export.assert_awaited_once_with(["-1001"], plan="July", enrich_profiles=True, password=None)

        form = urlencode({"token": "test-token", "scope": "unknown"})
        self.assertIn("400 Bad Request", self.request("POST", "/members-batch", form, origin))
        form = urlencode({"token": "test-token", "scope": "selected_groups"})
        self.assertIn("400 Bad Request", self.request("POST", "/members-batch", form, origin))

        form = urlencode({"token": "test-token"})
        reply = self.request("POST", "/switch-session", form, origin)
        self.assertIn("Локальный вход будет удалён", reply)
        self.assertTrue(self.runtime.shutdown_called)
        self.assertTrue(session_ops.switch_requested(self.service.home))

    def test_block_create_assign_select_and_escape(self):
        headers = {"Origin": "http://127.0.0.1:8765"}
        def post(path, **values):
            return self.request("POST", path, urlencode({"token": "test-token", **values}, doseq=True), headers)
        self.assertIn("303", post("/block-create", name="Friends & family"))
        block = next(iter(self.service.state["blocks"]))
        self.assertIn("303", post("/block-assign", block=block, chat=["12", "-1001"]))
        self.assertIn("Friends &amp; family", self.request("GET", "/"))
        self.assertIn("303", post("/select", block=block, selection=selection_fingerprint([])))
        self.assertEqual(set(self.service.state["selected"]), {"12", "-1001"})
        self.assertTrue((self.service.home / "chats.csv").is_file())
        self.assertIn("303", post("/block-delete", block=block))
        self.assertEqual(set(self.service.state["selected"]), {"12", "-1001"})

    def test_date_interval_and_add_chat_are_submitted(self):
        headers = {"Origin": "http://127.0.0.1:8765"}
        data = {"token": "test-token", "selection": selection_fingerprint([]),
                "start": "2026-01-01T10:30", "end": "2026-01-02T10:30"}
        self.assertIn("303", self.request("POST", "/interval", urlencode(data), headers))
        with patch.object(self.service, "export_selected", new_callable=AsyncMock) as export:
            asyncio.run(self.runtime.submitted[-1]())
        self.assertEqual(export.await_args.kwargs["start"].hour, 10)
        self.assertIsNotNone(export.await_args.kwargs["start"].utcoffset())
        self.assertEqual((export.await_args.kwargs["end"] - export.await_args.kwargs["start"]).total_seconds(), 86400)
        data["start"], data["end"] = data["end"], data["start"]
        self.assertIn("400", self.request("POST", "/interval", urlencode(data), headers))
        self.assertIn("303", self.request("POST", "/add-chat", urlencode({"token": "test-token", "reference": "@example"}), headers))
        with patch.object(self.service, "add_chat", new_callable=AsyncMock) as add:
            asyncio.run(self.runtime.submitted[-1]())
        add.assert_awaited_once_with("@example")

    def test_shutdown_requires_same_origin_and_token(self):
        with patch.object(self.runtime, "shutdown", create=True) as shutdown:
            body = urlencode({"token": "test-token"})
            self.assertIn("403", self.request("POST", "/shutdown", body, {"Origin": "https://evil.example"}))
            self.assertIn("403", self.request("POST", "/shutdown", urlencode({"token": "wrong"}), {"Origin": "http://127.0.0.1:8765"}))
            shutdown.assert_not_called()
            response = self.request("POST", "/shutdown", body, {"Origin": "http://127.0.0.1:8765"})
            self.assertIn("Signal Archive is closing", response)
            shutdown.assert_called_once()

    def test_instance_shutdown_requires_private_proof(self):
        from signal_archive.shortcut_launcher import prove
        self.runtime.instance_secret = '12' * 32
        challenge = '34' * 32
        origin = {'Origin': 'http://127.0.0.1:8765'}
        with patch.object(self.runtime, 'shutdown', create=True) as shutdown:
            self.assertIn('403', self.request('POST', '/instance-shutdown/' + challenge + '/' + '00' * 32, headers=origin))
            shutdown.assert_not_called()
            signature = prove(self.runtime.instance_secret, 'shutdown:' + challenge)
            self.assertIn('200 OK', self.request('POST', '/instance-shutdown/' + challenge + '/' + signature, headers=origin))
            shutdown.assert_called_once()

    def test_cross_origin_is_rejected(self):
        form = urlencode({"token": "test-token", "chat": "12"})
        response = self.request("POST", "/select", form, {"Origin": "http://attacker.example"})
        self.assertIn("403 Forbidden", response)
        self.assertEqual(self.service.state["selected"], [])

    def test_unavailable_selection_remains_visible_and_can_be_retained(self):
        self.service.state["selected"] = ["-999"]
        page = self.request("GET", "/")
        self.assertIn("Unavailable in latest refresh", page)
        form = urlencode({"token": "test-token", "chat": "-999", "selection": selection_fingerprint(["-999"])})
        response = self.request("POST", "/select", form, {"Origin": "http://127.0.0.1:8765"})
        self.assertIn("303 See Other", response)
        self.assertEqual(self.service.state["selected"], ["-999"])

    def test_stale_tab_cannot_overwrite_new_selection(self):
        stale = selection_fingerprint([])
        self.service.select(["12"])
        form = urlencode({"token": "test-token", "chat": "-1001", "selection": stale})
        response = self.request("POST", "/select", form, {"Origin": "http://127.0.0.1:8765"})
        self.assertIn("400 Bad Request", response)
        self.assertIn("selection changed", response)
        self.assertEqual(self.service.state["selected"], ["12"])

    def test_chat_list_is_bounded_at_bottom_and_selection_controls_remain_available(self):
        self.service.state["dialogs"] = [{"id": "-1", "title": "Group", "type": "group", "accessible": True}]
        page = self.request("GET", "/")
        self.assertLess(page.index("<h2>Latest activity</h2>"), page.index("<h2>Choose chats</h2>"))
        self.assertIn(".chat-list{max-height:", page)
        self.assertIn("overflow-y:auto", page)
        self.assertIn("class=chat-list tabindex=0", page)
        self.assertIn("name=chat value='-1'", page)
        self.assertIn("form=members name=chat_id value='-1'", page)
        self.assertIn("action=/select", page)
        self.assertIn("Save selection", page)

    def test_only_group_checkbox_is_visible_and_export_selection_sorts_first(self):
        self.service.state["dialogs"] = [
            {"id": "1", "title": "Selected chat", "type": "chat", "accessible": True},
            {"id": "2", "title": "Other chat", "type": "chat", "accessible": True},
        ]
        self.service.state["selected"] = ["1"]
        page = self.request("GET", "/")
        self.assertIn('.chat:has(input[name="chat"]:checked){order:-1}', page)
        self.assertIn("<input type=checkbox name=chat value='1' checked hidden><span class=chat-title>", page)
        self.assertIn("<input type=checkbox name=chat value='2' hidden><span class=chat-title>", page)
        self.assertNotIn("<label><input type=checkbox name=chat", page)
        self.assertIn("class=group-membership", page)
        self.assertLess(page.index("data-id='1'"), page.index("data-id='2'"))

    def test_public_chat_link_and_recent_results_are_visible(self):
        self.service.state["dialogs"] = [
            {"id": "123", "title": "Public", "type": "channel", "username": "public_name"},
            {"id": "456", "title": "Private", "type": "chat", "username": "bad\" onclick=evil"},
        ]
        page = self.request("GET", "/")
        self.assertIn("href='tg://resolve?domain=public_name'", page)
        self.assertIn("No public link", page)
        self.assertNotIn("domain=bad", page)
        self.assertIn("class=recent-list", page)
        self.assertLess(page.index("Latest activity"), page.index("<h2>Export</h2>"))

    def test_inaccessible_group_is_labeled_without_member_export_button(self):
        self.service.state["dialogs"] = [{"id": "-1", "title": "Old group", "type": "group", "accessible": False}]
        page = self.request("GET", "/")
        self.assertIn("group · inaccessible", page)
        self.assertNotIn("Export members</button>", page)

    def test_legacy_archive_is_shown_and_export_is_scheduled(self):
        db = Path(self.temp.name) / "legacy.sqlite"
        db.touch()
        chats = [{"account_id": 1, "id": "-123", "title": "Old <chat>"}]
        with patch.object(legacy_archive, "DEFAULT_DB", db), patch.object(legacy_archive, "list_chats", return_value=chats):
            self.assertNotIn("Old &lt;chat&gt;", self.request("GET", "/"))
            self.runtime.offline_legacy = True
            page = self.request("GET", "/")
            self.assertIn("Old &lt;chat&gt;", page)
            self.assertIn("action=/legacy-export", page)
            form = urlencode({"token": "test-token", "source": "1:-123", "days": "7"})
            response = self.request("POST", "/legacy-export", form, {"Origin": "http://127.0.0.1:8765"})
            self.assertIn("303 See Other", response)
            self.assertEqual(len(self.runtime.submitted), 1)

    def test_legacy_export_rejects_invalid_source(self):
        form = urlencode({"token": "test-token", "source": "bad", "days": "0"})
        response = self.request("POST", "/legacy-export", form, {"Origin": "http://127.0.0.1:8765"})
        self.assertIn("400 Bad Request", response)
        self.assertFalse(self.runtime.submitted)

    @unittest.skipUnless(importlib.util.find_spec("telethon"), "Telethon is not installed")
    def test_legacy_interface_exports_existing_database_without_telegram(self):
        db = Path(self.temp.name) / "old.sqlite"
        connection = sqlite3.connect(db)
        connection.executescript("""
            CREATE TABLE chats (account_id INTEGER, id INTEGER, title TEXT, type TEXT, username TEXT);
            CREATE TABLE messages (account_id INTEGER, chat_id INTEGER, id INTEGER, date TEXT,
                sender_id INTEGER, sender_name TEXT, text TEXT, reply_to_msg_id INTEGER,
                edit_date TEXT, is_deleted INTEGER);
            INSERT INTO chats VALUES (1, -123, 'Old chat', 'group', NULL);
            INSERT INTO messages VALUES (1, -123, 4, '2026-01-01', 7, 'Alice', 'hello', NULL, NULL, 0);
        """)
        connection.close()
        with patch.object(legacy_archive, "DEFAULT_DB", db):
            self.runtime.offline_legacy = True
            page = self.request("GET", "/")
            self.assertIn("Old chat", page)
            form = urlencode({"token": "test-token", "source": "1:-123", "days": "0"})
            response = self.request("POST", "/legacy-export", form, {"Origin": "http://127.0.0.1:8765"})
            self.assertIn("303 See Other", response)
            result = asyncio.run(self.runtime.submitted[0]())
        self.assertEqual(result["messages"], 1)
        self.assertEqual(next(csv.DictReader(io.StringIO(zipfile.ZipFile(result["path"]).read(result["file"]).decode("utf-8-sig"))))["text"], "hello")
        self.assertEqual(self.service.state["last_export_old_archive"]["status"], "success")

    def test_offline_mode_hides_telegram_actions_and_rejects_requests(self):
        self.runtime.offline_legacy = True
        page = self.request("GET", "/")
        self.assertIn("Офлайн: старый архив", page)
        self.assertIn("<section data-page=main hidden data-offline-disabled=yes><h2>Choose chats", page)
        form = urlencode({"token": "test-token"})
        response = self.request("POST", "/refresh", form, {"Origin": "http://127.0.0.1:8765"})
        self.assertIn("400 Bad Request", response)
        self.assertFalse(self.runtime.submitted)

    def test_open_folder_uses_configured_path(self):
        with patch("signal_archive.direct_app.open_exports") as opener:
            form = urlencode({"token": "test-token", "path": "/untrusted"})
            response = self.request("POST", "/open-exports", form, {"Origin": "http://127.0.0.1:8765"})
            self.assertIn("303 See Other", response)
            opener.assert_called_once_with(self.service.exports)

    def test_session_lock_rejects_second_owner(self):
        path = Path(self.temp.name) / "lock"
        with SessionLock(path):
            with self.assertRaisesRegex(RuntimeError, "already running"):
                with SessionLock(path):
                    pass


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        shortcut = patch('signal_archive.shortcuts.ensure_application_shortcut')
        shortcut.start()
        self.addCleanup(shortcut.stop)
        # Runtime tests build isolated workspaces; do not attach the developer's
        # real legacy Telegram Archive session to them.
        discovery = patch('signal_archive.session.discover_existing_session', return_value=None)
        discovery.start()
        self.addCleanup(discovery.stop)

    @unittest.skipUnless(importlib.util.find_spec("telethon"), "Telethon is not installed")
    def test_offline_archive_starts_without_credentials_or_telegram_client(self):
        class Server:
            server_port = 8765
            def __init__(self, address, handler): pass
            def serve_forever(self): pass
            def shutdown(self): pass
            def server_close(self): pass
        class Thread:
            def __init__(self, **kwargs): pass
            def start(self): asyncio.get_event_loop().call_soon(asyncio.get_event_loop().stop)
            def join(self): pass
        with tempfile.TemporaryDirectory() as tmp, \
             patch("telethon.TelegramClient") as client, \
             patch("signal_archive.direct_app.credentials") as creds, \
             patch("signal_archive.direct_app.ThreadingHTTPServer", Server), \
             patch("signal_archive.direct_app.threading.Thread", Thread), \
             patch("signal_archive.direct_app.os.umask"):
            run(Path(tmp) / "state", Path(tmp) / "exports", open_browser=False, offline_legacy=True)
            client.assert_not_called()
            creds.assert_not_called()

    @unittest.skipUnless(importlib.util.find_spec("telethon"), "Telethon is not installed")
    def test_existing_session_never_requests_login_code(self):
        from telethon import errors
        async def check():
            client = AsyncMock()
            client.connect = AsyncMock()
            client.get_me = AsyncMock(return_value=types.SimpleNamespace(id=42))
            client.start = AsyncMock()
            result = await _connect_client(client, "+1234567890", require_existing_auth=True)
            self.assertEqual(result.id, 42)
            client.start.assert_not_awaited()
            client.side_effect = errors.AuthKeyUnregisteredError(request=None)
            with self.assertRaisesRegex(RuntimeError, "не авторизована"):
                await _connect_client(client, "+1234567890", require_existing_auth=True)
            client.start.assert_not_awaited()
            client.side_effect = errors.FloodWaitError(request=None, capture=120)
            with self.assertRaises(errors.FloodWaitError):
                await _connect_client(client, "+1234567890", require_existing_auth=True)
            client.start.assert_not_awaited()
        asyncio.run(check())

    @unittest.skipUnless(importlib.util.find_spec("telethon"), "Telethon is not installed")
    def test_known_telegram_start_errors_have_recovery_guidance(self):
        from telethon import TelegramClient, errors
        from telethon.sessions import MemorySession
        cases = [(errors.SessionRevokedError, "forget-telegram-login"),
                 (errors.ApiIdInvalidError, "API credentials"),
                 (errors.PhoneNumberBannedError, "banned")]
        for error_type, expected in cases:
            with self.subTest(error=error_type.__name__), tempfile.TemporaryDirectory() as tmp:
                client = TelegramClient(MemorySession(), 12345, "a" * 32)
                home = Path(tmp) / "state"
                home.mkdir()
                state_path = home / "direct-state.json"
                state_path.write_text(json.dumps({"account_id": 42, "selected": ["1"], "checkpoints": {"1": 3}}))
                with patch("telethon.TelegramClient", return_value=client), \
                     patch.object(client, "_start", new_callable=AsyncMock, side_effect=error_type(request=None)), \
                     patch.object(client, "_disconnect_coro", new_callable=AsyncMock) as disconnect, \
                     patch("signal_archive.direct_app.credentials", return_value=(12345, "a" * 32, "+1234567890")), \
                     patch("signal_archive.direct_app.os.umask"):
                    with self.assertRaisesRegex(RuntimeError, expected):
                        run(home, Path(tmp) / "exports", open_browser=False)
                    disconnect.assert_awaited_once()
                self.assertEqual(json.loads(state_path.read_text())["checkpoints"], {"1": 3})

    def test_wait_for_cooldown_sleeps_locally_until_deadline(self):
        service = types.SimpleNamespace(state={"cooldown_until": "later"}, cooldown_remaining=unittest.mock.Mock(side_effect=[120, 60, 0]))
        with patch("signal_archive.direct_app.time.sleep") as sleep:
            wait_for_saved_cooldown(service)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [60, 60])

    @unittest.skipUnless(importlib.util.find_spec("telethon"), "Telethon is not installed")
    def test_autostart_retries_connection_after_wait(self):
        from telethon import TelegramClient, errors
        from telethon.sessions import MemorySession
        client = TelegramClient(MemorySession(), 12345, "a" * 32)
        class Server:
            server_port = 8765
            def __init__(self, address, handler): pass
            def serve_forever(self): pass
            def shutdown(self): pass
            def server_close(self): pass
        class Thread:
            def __init__(self, **kwargs): pass
            def start(self): asyncio.get_event_loop().call_soon(asyncio.get_event_loop().stop)
            def join(self): pass
        with tempfile.TemporaryDirectory() as tmp, \
             patch("telethon.TelegramClient", return_value=client), \
             patch.object(client, "_start", new_callable=AsyncMock, side_effect=[errors.FloodWaitError(request=None, capture=120), client]) as start, \
             patch.object(client, "get_me", new_callable=AsyncMock, return_value=types.SimpleNamespace(id=42)), \
             patch.object(client, "_disconnect_coro", new_callable=AsyncMock), \
             patch("signal_archive.direct_app.credentials", return_value=(12345, "a" * 32, "+1234567890")), \
             patch("signal_archive.direct_app.ThreadingHTTPServer", Server), \
             patch("signal_archive.direct_app.threading.Thread", Thread), \
             patch("signal_archive.direct_app.os.umask"), \
             patch("signal_archive.direct_app.wait_for_saved_cooldown") as wait:
            home = Path(tmp) / "state"
            run(home, Path(tmp) / "exports", open_browser=False, wait_for_cooldown=True)
            self.assertEqual(start.await_count, 2)
            self.assertEqual(wait.call_count, 2)  # Before connection, and after the simulated wait.
            self.assertEqual(json.loads((home / "direct-state.json").read_text())["account_id"], 42)

    @unittest.skipUnless(importlib.util.find_spec("telethon"), "Telethon is not installed")
    def test_startup_wait_is_saved_and_next_start_does_not_connect(self):
        from telethon import TelegramClient, errors
        from telethon.sessions import MemorySession
        client = TelegramClient(MemorySession(), 12345, "a" * 32)
        with tempfile.TemporaryDirectory() as tmp, \
             patch("telethon.TelegramClient", return_value=client) as factory, \
             patch.object(client, "_start", new_callable=AsyncMock, side_effect=errors.FloodWaitError(request=None, capture=120)) as start, \
             patch.object(client, "_disconnect_coro", new_callable=AsyncMock) as disconnect, \
             patch("signal_archive.direct_app.credentials", return_value=(12345, "a" * 32, "+1234567890")) as get_credentials, \
             patch("signal_archive.direct_app.os.umask"):
            home = Path(tmp) / "state"
            with self.assertRaisesRegex(RuntimeError, "pause until"):
                run(home, Path(tmp) / "exports", open_browser=False)
            state = json.loads((home / "direct-state.json").read_text())
            self.assertEqual(state["job"]["status"], "waiting")
            self.assertEqual(state["job"]["name"], "connect")
            with self.assertRaisesRegex(RuntimeError, "pause until"):
                run(home, Path(tmp) / "exports", open_browser=False)
            self.assertEqual(factory.call_count, 1)
            self.assertEqual(get_credentials.call_count, 1)
            start.assert_awaited_once()
            disconnect.assert_awaited_once()

    @unittest.skipUnless(importlib.util.find_spec("telethon"), "Telethon is not installed")
    def test_application_lifecycle_with_real_telethon_and_mocked_network(self):
        from telethon import TelegramClient
        from telethon.sessions import MemorySession
        client = TelegramClient(MemorySession(), 12345, "a" * 32)
        events = []
        class Server:
            server_port = 8765
            def __init__(self, address, handler): events.append("server created")
            def serve_forever(self): pass
            def shutdown(self): events.append("server stopped")
            def server_close(self): pass
        class Thread:
            def __init__(self, **kwargs): pass
            def start(self): asyncio.get_event_loop().call_soon(asyncio.get_event_loop().stop)
            def join(self): pass
        with tempfile.TemporaryDirectory() as tmp, \
             patch("telethon.TelegramClient", return_value=client), \
             patch.object(client, "_start", new_callable=AsyncMock, return_value=client) as start, \
             patch.object(client, "get_me", new_callable=AsyncMock, return_value=types.SimpleNamespace(id=42)), \
             patch.object(client, "_disconnect_coro", new_callable=AsyncMock) as disconnect, \
             patch("signal_archive.direct_app.credentials", return_value=(12345, "a" * 32, "+1234567890")), \
             patch("signal_archive.direct_app.ThreadingHTTPServer", Server), \
             patch("signal_archive.direct_app.threading.Thread", Thread), \
             patch("signal_archive.direct_app.os.umask"):
            home = Path(tmp) / "state"
            run(home, Path(tmp) / "exports", open_browser=False)
            self.assertEqual(json.loads((home / "direct-state.json").read_text())["account_id"], 42)
            start.assert_awaited_once()
            disconnect.assert_awaited_once()
            self.assertEqual(events, ["server created", "server stopped"])

    @unittest.skipUnless(importlib.util.find_spec("telethon"), "Telethon is not installed")
    def test_real_telethon_start_and_disconnect_are_awaited_on_running_loop(self):
        from telethon import TelegramClient
        from telethon.sessions import MemorySession
        async def check():
            client = TelegramClient(MemorySession(), 12345, "a" * 32)
            with patch.object(client, "_start", new_callable=AsyncMock, return_value=client) as start, \
                 patch.object(client, "get_me", new_callable=AsyncMock, return_value=types.SimpleNamespace(id=42)), \
                 patch.object(client, "_disconnect_coro", new_callable=AsyncMock) as disconnect:
                account = await _connect_client(client, "+1234567890")
                self.assertEqual(account.id, 42)
                start.assert_awaited_once()
                await _disconnect_client(client)
                disconnect.assert_awaited_once()
        asyncio.run(check())

    def test_cancel_manual_operation_cancels_future(self):
        runtime = Runtime(None, None)
        runtime.future = Future()
        runtime.cancel()
        self.assertTrue(runtime.future.cancelled())
        runtime.call = lambda operation: operation()
        with self.assertRaisesRegex(RuntimeError, "No operation is running"):
            runtime.cancel()

    def test_stopping_scheduled_export_prevents_immediate_restart(self):
        async def check():
            started = asyncio.Event()
            class Service:
                def __init__(self):
                    self.lock = asyncio.Lock()
                    self.state = {"schedule": {"enabled": True, "time": "00:00"}, "selected": ["1"]}
                async def export_selected(self):
                    async with self.lock:
                        started.set()
                        await asyncio.Event().wait()
                def catalog_is_current(self):
                    return True
                def save(self):
                    pass
            service = Service()
            runtime = Runtime(asyncio.get_running_loop(), service)
            runtime.call = lambda operation: operation()
            task = asyncio.create_task(scheduler(runtime))
            try:
                await asyncio.wait_for(started.wait(), 1)
                runtime.cancel()
                for _ in range(10):
                    await asyncio.sleep(0)
                    if service.state["schedule"].get("failed_day"):
                        break
                self.assertIn("failed_day", service.state["schedule"])
                self.assertIsNone(runtime.scheduled_task)
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        asyncio.run(check())


class CredentialTests(unittest.TestCase):
    def test_forget_login_removes_only_this_workspace_credentials(self):
        class Keyring:
            def __init__(self): self.values = {}
            def get_password(self, service, account): return self.values.get((service, account))
            def delete_password(self, service, account): del self.values[(service, account)]
        keyring = Keyring()
        with tempfile.TemporaryDirectory() as tmp, patch("signal_archive.direct_app._keyring", return_value=keyring):
            home = Path(tmp)
            account = _secret_account(home)
            keyring.values[("SignalArchiveDirect", account)] = "secret"
            keyring.values[("SignalArchiveDirect", "api-hash")] = "legacy shared"
            (home / "account.json").write_text(json.dumps({"keyring_account": account}))
            (home / "telegram.session").write_text("session")
            (home / "direct-state.json").write_text("selection")
            forget_telegram_login(home)
            self.assertFalse((home / "account.json").exists())
            self.assertFalse((home / "telegram.session").exists())
            self.assertEqual((home / "direct-state.json").read_text(), "selection")
            self.assertEqual(keyring.values[("SignalArchiveDirect", "api-hash")], "legacy shared")
            self.assertNotIn(("SignalArchiveDirect", account), keyring.values)

    def test_forget_login_refuses_running_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with SessionLock(home / "session.lock"):
                with self.assertRaisesRegex(RuntimeError, "already running"):
                    forget_telegram_login(home)

    @unittest.skipIf(os.name == "nt", "POSIX file modes")
    def test_existing_session_and_sidecars_become_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            for suffix in (".session", ".session-wal", ".session-shm", ".session-journal"):
                path = home / ("telegram" + suffix)
                path.write_text("sensitive")
                path.chmod(0o644)
            protect_existing_session(home)
            for path in home.iterdir():
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    @unittest.skipIf(os.name == "nt", "POSIX symlinks")
    def test_session_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            original = home / "unrelated"
            original.write_text("data")
            original.chmod(0o644)
            (home / "telegram.session").symlink_to(original)
            with self.assertRaisesRegex(RuntimeError, "symbolic link"):
                protect_existing_session(home)
            self.assertEqual(original.stat().st_mode & 0o777, 0o644)

    def test_legacy_secret_migrates_once_and_missing_new_secret_fails_closed(self):
        class Keyring:
            def __init__(self): self.values = {("SignalArchiveDirect", "api-hash"): "a" * 32}
            def get_password(self, service, account): return self.values.get((service, account))
            def set_password(self, service, account, value): self.values[(service, account)] = value
        keyring = Keyring()
        with tempfile.TemporaryDirectory() as tmp, patch("signal_archive.direct_app._keyring", return_value=keyring):
            home = Path(tmp)
            (home / "account.json").write_text(json.dumps({"api_id": 1, "phone": "+1234567890"}))
            self.assertEqual(credentials(home)[1], "a" * 32)
            account = json.loads((home / "account.json").read_text())["keyring_account"]
            del keyring.values[("SignalArchiveDirect", account)]
            with self.assertRaisesRegex(RuntimeError, "missing from the OS keyring"):
                credentials(home)

    def test_two_workspaces_keep_separate_hashes(self):
        class Keyring:
            def __init__(self): self.values = {}
            def get_password(self, service, account): return self.values.get((service, account))
            def set_password(self, service, account, value): self.values[(service, account)] = value
        keyring = Keyring()
        with tempfile.TemporaryDirectory() as tmp, patch("signal_archive.direct_app._keyring", return_value=keyring):
            first, second = Path(tmp) / "first", Path(tmp) / "second"
            with patch("builtins.input", side_effect=["1", "+1234567890", "2", "+1234567891"]), patch("signal_archive.direct_app.getpass.getpass", side_effect=["a" * 32, "b" * 32]):
                credentials(first)
                credentials(second)
            self.assertEqual(credentials(first)[1], "a" * 32)
            self.assertEqual(credentials(second)[1], "b" * 32)


if __name__ == "__main__":
    unittest.main()
