import json
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from tg_observatory.api import ObservatoryApi


class ExportPacingTests(unittest.TestCase):
    def test_resume_keeps_only_ids_from_all_successful_fetch_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            api = ObservatoryApi(directory)
            seen = []
            async def export(job_id, params, checkpoint):
                rows = api._database.fetchall("SELECT telegram_message_id FROM export_source_messages WHERE export_job_id=? ORDER BY telegram_message_id", (job_id,))
                seen.append([row["telegram_message_id"] for row in rows])
                return {"records": len(rows)}
            def wait(job_id):
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    job = api._jobs.snapshot(job_id)
                    if job["state"] in {"completed", "failed"}:
                        # State becomes visible just before durable finish.
                        future = api._jobs.get(job_id).future
                        if future is not None and future.done(): return job
                    time.sleep(0.005)
                self.fail("export did not finish")
            try:
                pages = [
                    {"items": [{"message_id": 1}], "has_more": True, "next_after_id": 1},
                    ConnectionError("offline"),
                    {"items": [{"message_id": 2}], "has_more": False, "next_after_id": 2},
                ]
                with patch("tg_observatory.api.MessageService.preview_range", new=AsyncMock(side_effect=pages)) as preview, \
                     patch("tg_observatory.api.ExportService.export_dialog", new=AsyncMock(side_effect=export)), \
                     patch("tg_observatory.api._between_export_pages", new=AsyncMock()):
                    response = json.loads(api.dispatch(json.dumps({"operation": "messages.export", "params": {"dialog_id": 1, "fetch_source": True}})))
                    job_id = response["data"]["job_id"]
                    self.assertEqual(wait(job_id)["state"], "failed")
                    resumed = json.loads(api.dispatch(json.dumps({"operation": "jobs.resume", "params": {"job_id": job_id, "api_id": 1, "api_hash": "a" * 32}})))
                    self.assertTrue(resumed["ok"], resumed)
                    self.assertEqual(wait(job_id)["state"], "completed")
                    self.assertEqual(seen, [[1, 2]])
                    self.assertEqual(preview.await_args_list[-1].args[0]["after_id"], 1)
                    self.assertEqual(api._database.fetchone("SELECT count(*) AS n FROM export_source_messages WHERE export_job_id=?", (job_id,))["n"], 0)
            finally:
                api.close()

    def test_waits_only_between_nonfinal_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            api = ObservatoryApi(directory)
            try:
                pages = [
                    {"items": [{"message_id": 1}], "has_more": True, "next_after_id": 1},
                    {"items": [], "has_more": False, "next_after_id": 1},
                ]
                with patch("tg_observatory.api.MessageService.preview_range", new=AsyncMock(side_effect=pages)), \
                     patch("tg_observatory.api.ExportService.export_dialog", new=AsyncMock(return_value={"records": 1})), \
                     patch("tg_observatory.api._between_export_pages", new=AsyncMock()) as pause:
                    response = json.loads(api.dispatch(json.dumps({"operation": "messages.export", "params": {"dialog_id": 1, "fetch_source": True}})))
                    job_id = response["data"]["job_id"]
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline:
                        job = api._jobs.snapshot(job_id)
                        if job["state"] in {"completed", "failed"}:
                            break
                        time.sleep(0.005)
                    self.assertEqual(job["state"], "completed", job)
                    self.assertEqual(pause.await_count, 1)
            finally:
                api.close()


if __name__ == "__main__":
    unittest.main()
