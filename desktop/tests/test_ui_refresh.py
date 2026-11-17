import json
import shutil
import subprocess
import unittest

from signal_archive.direct_app import UI_SCRIPT


HARNESS = r'''
const vm = require('vm');
const fs = require('fs');
const cfg = JSON.parse(process.argv[1]);
const code = fs.readFileSync(0, 'utf8');
let changeHandler = () => {};
const checkbox = {checked: false, addEventListener: (name, callback) => { changeHandler = callback; }};
const refresh = {dataset: {started: cfg.initial || ''}, querySelector: () => ({value: 'token'})};
const status = {textContent: ''};
const notice = {textContent: '', scrollIntoView() {}};
const chat = {hidden: false, dataset: {search: 'name', type: 'group'}, querySelector: () => checkbox};
const storage = {value: cfg.pending ? JSON.stringify(cfg.pending) : null,
    getItem() { return this.value; }, setItem(key, value) { this.value = value; }, removeItem() { this.value = null; }};
let reloads = 0;
const ctx = {
    document: {getElementById(id) { return {refreshForm: refresh, status, selectionNotice: notice, search: {value:''}, kind:{value:''}}[id]; },
        querySelectorAll(selector) { return selector.startsWith('input[type=') ? [] : selector.startsWith('.chat input') ? [checkbox] : [chat]; }},
    sessionStorage: storage, location: {reload() { reloads++; }},
    fetch() { return Promise.resolve({json() { return Promise.resolve({job: cfg.job || {}}); }}); },
    setInterval() {}, console
};
vm.runInNewContext(code, ctx);
if (cfg.dirty) changeHandler();
if (cfg.bulk) ctx.selectVisible(true);
let submit = cfg.submit ? ctx.rememberRefresh() : null;
let exportAllowed = cfg.exportAttempt ? ctx.ensureSelectionSaved() : null;
setImmediate(() => { process.stdout.write(JSON.stringify({reloads, status:status.textContent, notice:notice.textContent, stored:storage.value, submit, exportAllowed})); });
'''


@unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
class RefreshUiTests(unittest.TestCase):
    def run_ui(self, **config):
        result = subprocess.run(["node", "-e", HARNESS, json.dumps(config)], input=UI_SCRIPT,
                                capture_output=True, text=True, check=True, timeout=5)
        return json.loads(result.stdout)

    def test_new_completed_refresh_reloads_without_unsaved_selection(self):
        result = self.run_ui(pending={"token": "token", "started": "old"},
                             job={"name": "refresh dialogs", "status": "success", "started_at": "new"})
        self.assertEqual(result["reloads"], 1)
        self.assertIsNone(result["stored"])

    def test_unsaved_selection_is_not_reloaded_or_discarded(self):
        result = self.run_ui(dirty=True, pending={"token": "token", "started": "old"},
                             job={"name": "refresh dialogs", "status": "success", "started_at": "new"})
        self.assertEqual(result["reloads"], 0)
        self.assertIn("Save your selection", result["status"])

    def test_add_chat_reload_preserves_dirty_selection(self):
        pending = {"token": "token", "started": "old", "kind": "add chat"}
        job = {"name": "add chat", "status": "success", "started_at": "new"}
        self.assertEqual(self.run_ui(pending=pending, job=job)["reloads"], 1)
        dirty = self.run_ui(dirty=True, pending=pending, job=job)
        self.assertEqual(dirty["reloads"], 0)
        self.assertIn("Save your selection", dirty["status"])

    def test_refresh_is_blocked_if_selection_already_dirty(self):
        result = self.run_ui(dirty=True, submit=True)
        self.assertFalse(result["submit"])
        self.assertIn("Save your selection", result["notice"])

    def test_export_requires_saving_changed_selection(self):
        self.assertTrue(self.run_ui(exportAttempt=True)["exportAllowed"])
        for config in ({"dirty": True}, {"bulk": True}):
            with self.subTest(config=config):
                result = self.run_ui(exportAttempt=True, **config)
                self.assertFalse(result["exportAllowed"])
                self.assertIn("Save your selection", result["notice"])

    def test_previous_or_failed_job_does_not_reload(self):
        pending = {"token": "token", "started": "old"}
        stale = self.run_ui(pending=pending, job={"name": "refresh dialogs", "status": "success", "started_at": "old"})
        self.assertEqual(stale["reloads"], 0)
        failed = self.run_ui(pending=pending, job={"name": "refresh dialogs", "status": "failed", "started_at": "new", "error": "offline"})
        self.assertEqual(failed["reloads"], 0)
        self.assertIn("offline", failed["status"])
        self.assertIsNone(failed["stored"])

    def test_group_tabs_show_all_and_do_not_change_export_selection(self):
        harness = r'''
const vm = require('vm'), fs = require('fs');
const checked = {checked:true, addEventListener() {}};
const chats = [
  {hidden:false, dataset:{search:'work',type:'group',groups:'team'}, querySelector(){return {checked:false};}},
  {hidden:false, dataset:{search:'solo',type:'chat',groups:''}, querySelector(){return {checked:false};}},
];
const tab = {setAttribute(){}};
const ctx = {document:{getElementById(id){return {refreshForm:{dataset:{started:''},querySelector(){return {value:'token'}}},search:{value:''},kind:{value:''},status:{},selectionNotice:{}}[id]},querySelectorAll(s){if(s.startsWith('input[type='))return [];if(s.startsWith('.chat input'))return [checked];if(s==='.group-tab')return [tab];if(s==='.chat')return chats;return []}},sessionStorage:{getItem(){return null}},fetch(){return Promise.resolve({json(){return Promise.resolve({})}})},location:{reload(){}},setInterval(){},console};
vm.runInNewContext(fs.readFileSync(0,'utf8'),ctx);
ctx.showGroup('team',tab);const team=chats.map(c=>c.hidden);
ctx.showGroup('ungrouped',tab);const ungrouped=chats.map(c=>c.hidden);
process.stdout.write(JSON.stringify({team,ungrouped,checked:checked.checked}));
'''
        result = subprocess.run(["node", "-e", harness], input=UI_SCRIPT, capture_output=True, text=True, check=True, timeout=5)
        self.assertEqual(json.loads(result.stdout), {"team": [False, False], "ungrouped": [False, False], "checked": True})
