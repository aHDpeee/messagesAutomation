import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from telethon import errors
from tg_observatory.runtime.jobs import Job, JobManager
from tg_observatory.telegram.client import TelegramGateway


class QrAuthTests(unittest.TestCase):
    def test_waiter_is_started_before_display_and_completes(self):
        async def scenario():
            ready, accepted = asyncio.Event(), asyncio.Event()
            class QR:
                url = "tg://login?token=secret"
                expires = datetime.now(UTC) + timedelta(seconds=30)
                async def wait(self):
                    ready.set()
                    await accepted.wait()
            client = SimpleNamespace(qr_login=AsyncMock(return_value=QR()), get_me=AsyncMock(return_value=SimpleNamespace(id=42, username=None, first_name="A", last_name=None)))
            gateway = TelegramGateway("unused")
            gateway._connected_client = AsyncMock(return_value=client)
            gateway._client = client
            token = await gateway.start_qr_login(1, "a" * 32)
            self.assertTrue(ready.is_set())
            self.assertTrue(token["url"].startswith("tg://login?token="))
            accepted.set()
            status, profile = await gateway.finish_qr_login()
            self.assertEqual((status, profile.telegram_id), ("authenticated", 42))
        asyncio.run(scenario())

    def test_two_factor_and_expired_token_are_reported(self):
        async def scenario(error, expected):
            gateway = TelegramGateway("unused")
            async def wait(): raise error
            gateway._qr_task = asyncio.create_task(wait())
            self.assertEqual(await gateway.finish_qr_login(), (expected, None))
        asyncio.run(scenario(errors.SessionPasswordNeededError(request=None), "password_required"))
        asyncio.run(scenario(asyncio.TimeoutError(), "expired"))

    def test_login_token_is_not_written_to_job_repository(self):
        async def scenario():
            repository = Mock()
            manager = JobManager(None, repository)
            job = Job("qr", job_type="account.qr_start")
            async def work(): return {"url": "tg://login?token=secret"}
            await manager._execute(job, work())
            self.assertIsNone(repository.finish.call_args.kwargs["result"])
            self.assertEqual(job.result["url"], "tg://login?token=secret")
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
