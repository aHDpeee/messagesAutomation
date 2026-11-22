import asyncio
import json
import tempfile
import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from tg_observatory.api import ObservatoryApi
from tg_observatory.exceptions import InvalidRequest
from tg_observatory.models.account import AccountProfile
from tg_observatory.models.dialog import DialogSummary
from tg_observatory.telegram.client import TelegramGateway


class PreviewClient:
    def __init__(self, messages):
        self.messages = messages
        self.requests = []

    async def __call__(self, request):
        from telethon.tl.functions.updates import GetStateRequest
        assert isinstance(request, GetStateRequest)
        return SimpleNamespace()

    async def iter_messages(self, peer, **kwargs):
        self.requests.append(kwargs)
        rows = [message for message in self.messages
                if message.id > kwargs["min_id"]
                and (kwargs["offset_date"] is None or message.date > kwargs["offset_date"])]
        for message in rows[:kwargs["limit"]]:
            yield message


class TelegramRangePreviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.start = datetime(2026, 1, 2, tzinfo=UTC)
        self.messages = [SimpleNamespace(
            id=index + 1, date=self.start + timedelta(seconds=index), message=f"Preview {index}",
        ) for index in range(205)]
        self.client = PreviewClient(self.messages)
        self.gateway = TelegramGateway("unused")

        async def connected(*args):
            return self.client

        self.gateway._connected_client = connected

    async def test_first_page_is_bounded_and_does_not_require_indexed_messages(self):
        page = await self.gateway.preview_range_page(123, "a" * 32, 456, limit=100)
        self.assertEqual([item["message_id"] for item in page["items"]], list(range(1, 101)))
        self.assertTrue(page["has_more"])
        self.assertEqual(page["next_after_id"], 100)
        self.assertIsNone(page["total"])
        self.assertEqual(page["source"], "telegram")
        self.assertEqual(self.client.requests[0]["limit"], 101)
        self.assertTrue(self.client.requests[0]["reverse"])

    async def test_date_boundaries_are_start_inclusive_end_exclusive_and_cursor_has_no_duplicates(self):
        page = await self.gateway.preview_range_page(
            123, "a" * 32, 456, limit=2,
            period_start=self.start, period_end=self.start + timedelta(seconds=4),
        )
        self.assertEqual([item["message_id"] for item in page["items"]], [1, 2])
        self.assertEqual(self.client.requests[0]["offset_date"], self.start - timedelta(seconds=1))
        following = await self.gateway.preview_range_page(
            123, "a" * 32, 456, limit=2, after_id=page["next_after_id"],
            period_start=self.start, period_end=self.start + timedelta(seconds=4),
        )
        self.assertEqual([item["message_id"] for item in following["items"]], [3, 4])
        self.assertFalse(following["has_more"])

    async def test_pre_start_overlap_can_advance_even_when_visible_page_is_empty(self):
        self.client.messages = [SimpleNamespace(
            id=index + 1, date=self.start - timedelta(milliseconds=500), message="Before start",
        ) for index in range(102)]
        page = await self.gateway.preview_range_page(
            123, "a" * 32, 456, period_start=self.start, limit=100,
        )
        self.assertEqual(page["items"], [])
        self.assertTrue(page["has_more"])
        self.assertEqual(page["next_after_id"], 101)

    async def test_cancellation_and_flood_wait_propagate(self):
        class FloodWaitError(Exception):
            pass

        async def fail(*args, **kwargs):
            raise FloodWaitError()
            yield

        self.client.iter_messages = fail
        with self.assertRaises(FloodWaitError):
            await self.gateway.preview_range_page(123, "a" * 32, 456)

        async def cancel(*args, **kwargs):
            raise asyncio.CancelledError()
            yield

        self.client.iter_messages = cancel
        with self.assertRaises(asyncio.CancelledError):
            await self.gateway.preview_range_page(123, "a" * 32, 456)

    async def test_invalid_limit_is_rejected_before_request(self):
        with self.assertRaises(InvalidRequest):
            await self.gateway.preview_range_page(123, "a" * 32, 456, limit=101)
        self.assertEqual(self.client.requests, [])

    async def test_incomplete_or_out_of_order_messages_abort_export(self):
        async def missing_date(*args, **kwargs):
            yield SimpleNamespace(id=1, date=None, message="unknown")
        self.client.iter_messages = missing_date
        with self.assertRaisesRegex(InvalidRequest, "without an ID or date"):
            await self.gateway.preview_range_page(123, "a" * 32, 456)
        async def out_of_order(*args, **kwargs):
            yield SimpleNamespace(id=2, date=self.start, message="second")
            yield SimpleNamespace(id=1, date=self.start, message="first")
        self.client.iter_messages = out_of_order
        with self.assertRaisesRegex(InvalidRequest, "out of order"):
            await self.gateway.preview_range_page(123, "a" * 32, 456)


class PreviewApiTests(unittest.TestCase):
    def test_job_caches_preview_without_persisting_api_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            api = ObservatoryApi(directory)
            try:
                profile = AccountProfile(123, "test_user", "Test", None, None)
                api._accounts.save_profile(profile)
                account_id = api._accounts.local_id(123)
                api._dialogs.sync(account_id, [DialogSummary(
                    456, 456, "private", "Test conversation", None, 0, None, None,
                )])

                async def status(*args):
                    return profile

                async def preview(*args, **kwargs):
                    return {"items": [{"message_id": 10, "date_utc": "2026-01-02T00:00:00Z",
                                       "text": "Cached preview", "sender_id": 88}],
                            "has_more": False, "next_after_id": 10, "total": None,
                            "earliest": None, "latest": None, "source": "telegram"}

                api._gateway.account_status = status
                api._gateway.preview_range_page = preview
                accepted = json.loads(api.dispatch(json.dumps({
                    "operation": "messages.preview_range", "params": {
                        "dialog_id": 456, "api_id": 12345, "api_hash": "a" * 32,
                    },
                })))
                job_id = accepted["data"]["job_id"]
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    job = api._jobs.snapshot(job_id)
                    if job["state"] in {"completed", "failed"}:
                        break
                    time.sleep(0.005)
                self.assertEqual(job["state"], "completed", job)
                self.assertEqual(job["result"]["source"], "telegram")
                row = api._database.fetchone("SELECT text FROM messages")
                self.assertEqual(row["text"], "Cached preview")
                self.assertNotIn("a" * 32, "\n".join(api._database._connection.iterdump()))
                self.assertFalse(list(Path(directory).glob("**/*.session")))
            finally:
                api.close()
