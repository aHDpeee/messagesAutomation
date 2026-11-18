import unittest
import json
import shutil
import subprocess
import io
import types
from datetime import datetime, timezone

from signal_archive.schedule_timing import summary


class ScheduleTimingTests(unittest.TestCase):
    def test_status_endpoint_includes_schedule_and_header_links_to_settings(self):
        from signal_archive.direct_app import make_handler
        state = {"selected": ["1"], "schedule": {"enabled": False}, "job": {}, "dialogs": []}
        service = types.SimpleNamespace(state=state)
        runtime = types.SimpleNamespace(service=service, call=lambda f: f())
        handler_type = make_handler(runtime, "test-token")
        def get(path):
            handler = handler_type.__new__(handler_type)
            handler.path = path
            handler.server = types.SimpleNamespace(server_port=8765)
            handler.headers = {"Host": "127.0.0.1:8765"}
            handler.rfile, handler.wfile = io.BytesIO(), io.BytesIO()
            handler.request_version = "HTTP/1.1"
            handler.requestline = "GET " + path + " HTTP/1.1"
            handler.do_GET()
            return handler.wfile.getvalue().decode()
        status = json.loads(get("/status").split("\r\n\r\n", 1)[1])
        self.assertEqual(status["schedule_timing"]["next_at"], None)
        # Inspect rendered template without requiring optional desktop services.
        from signal_archive import direct_app
        source = __import__("inspect").getsource(direct_app.make_handler)
        self.assertIn("id=schedule-summary href='#schedule-settings'", source)
        self.assertIn("id=schedule-settings", source)

    def test_catch_up_then_next_day(self):
        now = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
        state = {"selected": ["1"], "schedule": {"enabled": True, "time": "03:15"}}
        self.assertEqual(summary(state, now)["next_at"], now.isoformat())
        state["schedule"]["last_day"] = "2026-01-02"
        self.assertEqual(summary(state, now)["next_at"], "2026-01-03T03:15:00+00:00")

    def test_disabled_and_latest_success(self):
        now = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
        state = {"selected": ["1"], "schedule": {"enabled": False},
                 "last_export_messages": {"finished_at": "2026-01-01T10:00:00+00:00"},
                 "last_export_group_members": {"finished_at": "2026-01-02T09:00:00+00:00"}}
        self.assertEqual(summary(state, now), {"next_at": None, "last_at": "2026-01-02T09:00:00+00:00"})

    @unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
    def test_browser_countdown_formats_units_and_hides_recent_last_export(self):
        from signal_archive.direct_app import UI_SCRIPT
        start = UI_SCRIPT.index("let scheduleTiming = {};")
        end = UI_SCRIPT.index("function update()", start)
        code = UI_SCRIPT[start:end]
        harness = r'''
const vm = require('vm');
const fs = require('fs');
const fixed = Date.parse('2026-01-02T12:00:00Z');
class FixedDate extends Date { static now() { return fixed; } }
const element = {textContent:'', hidden:false};
const ctx = {Date:FixedDate, document:{getElementById(){return element;}}};
vm.runInNewContext(fs.readFileSync(0,'utf8'), ctx);
function render(next, last) {
  vm.runInNewContext(`scheduleTiming = ${JSON.stringify({next_at:next,last_at:last})}; updateScheduleSummary()`,ctx);
  return {text:element.textContent, hidden:element.hidden};
}
process.stdout.write(JSON.stringify([
 render('2026-01-02T12:20:00Z','2026-01-02T01:00:00Z'),
 render('2026-01-02T12:00:42Z','2025-12-31T11:00:00Z'),
 render(null,null)
]));
'''
        result = subprocess.run(["node", "-e", harness], input=code, text=True,
                                capture_output=True, check=True, timeout=5)
        values = json.loads(result.stdout)
        self.assertEqual(values[0], {"text": "next export in 20m", "hidden": False})
        self.assertEqual(values[1], {"text": "next export in 42s · last export 2d ago", "hidden": False})
        self.assertEqual(values[2], {"text": "", "hidden": True})
