import asyncio
import tempfile
import unittest
from pathlib import Path

from telethon import errors, functions
from tg_observatory.exceptions import ConversationUnavailable, EmptyMessageRange, InvalidRequest
from tg_observatory.models.account import AccountProfile
from tg_observatory.storage.database import Database
from tg_observatory.storage.repositories.accounts import AccountRepository
from tg_observatory.telegram.client import _user_authorized
from tg_observatory.runtime.jobs import Job, JobManager, JobState


class AuthorizationGuardTests(unittest.TestCase):
    def test_code_request_reports_delivery_without_code_or_phone(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from telethon.tl.types.auth import SentCodeTypeApp, SentCodeTypeSms
        from tg_observatory.telegram.client import TelegramGateway
        async def scenario():
            gateway = TelegramGateway("unused")
            client = SimpleNamespace(send_code_request=AsyncMock(side_effect=[
                SimpleNamespace(type=SentCodeTypeApp(5), phone_code_hash="secret", timeout=30),
                SimpleNamespace(type=SentCodeTypeSms(5), phone_code_hash="secret2", timeout=None),
            ]))
            gateway._connected_client = AsyncMock(return_value=client)
            app = await gateway.request_code(1, "a" * 32, "+123456789")
            sms = await gateway.request_code(1, "a" * 32, "+123456789")
            self.assertEqual(app, {"delivery": "telegram", "timeout": 30})
            self.assertEqual(sms, {"delivery": "sms", "timeout": None})
            self.assertNotIn("secret", str(app))
        asyncio.run(scenario())

    def test_connection_error_has_safe_retryable_message(self):
        async def scenario():
            manager = JobManager(None)
            async def operation(): raise ConnectionError("sensitive internal detail")
            job = Job("network")
            await manager._execute(job, operation())
            self.assertEqual(job.error["code"], "network_unavailable")
            self.assertNotIn("sensitive", job.error["message"])
        asyncio.run(scenario())

    def test_sign_in_errors_have_specific_codes(self):
        async def scenario():
            manager = JobManager(None)
            for error, expected in ((errors.PhoneCodeInvalidError(request=None), "invalid_code"),
                                    (errors.PhoneCodeExpiredError(request=None), "expired_code"),
                                    (errors.PhoneNumberInvalidError(request=None), "invalid_phone"),
                                    (errors.SendCodeUnavailableError(request=None), "code_unavailable"),
                                    (errors.UpdateAppToLoginError(request=None), "official_app_required")):
                async def operation(): raise error
                job = Job(expected)
                await manager._execute(job, operation())
                self.assertEqual(job.error["code"], expected)
                self.assertEqual(job.error["exception_type"], type(error).__name__)
        asyncio.run(scenario())

    def test_engine_is_idle_only_after_job_future_finishes(self):
        from concurrent.futures import Future
        class MockTask:
            finished = False
            def done(self): return self.finished
        manager = JobManager(None)
        self.assertTrue(manager.is_idle())
        future = Future()
        job = Job("closing")
        job.future = future
        task = MockTask()
        job.task = task
        job.state = JobState.COMPLETED  # Cleanup can still be writing to SQLite.
        manager._jobs[job.job_id] = job
        self.assertFalse(manager.is_idle())
        future.set_result(None)
        self.assertFalse(manager.is_idle())
        task.finished = True
        self.assertTrue(manager.is_idle())

    def test_telegram_invite_error_has_safe_specific_code(self):
        async def scenario():
            manager = JobManager(None)
            async def operation(): raise errors.InviteHashExpiredError(request=None)
            job = Job("expired")
            await manager._execute(job, operation())
            self.assertEqual(job.error["code"], "invite_invalid")
            self.assertNotIn("request", job.error["message"])
        asyncio.run(scenario())
    def test_empty_range_has_distinct_machine_readable_code(self):
        async def scenario():
            manager = JobManager(None)
            async def operation(): raise EmptyMessageRange()
            job = Job("empty")
            await manager._execute(job, operation())
            self.assertEqual(job.error["code"], "empty_message_range")
        asyncio.run(scenario())

    def test_unavailable_chat_has_distinct_machine_readable_code(self):
        async def scenario():
            manager = JobManager(None)
            async def operation(): raise ConversationUnavailable()
            job = Job("missing")
            await manager._execute(job, operation())
            self.assertEqual(job.error["code"], "conversation_unavailable")
        asyncio.run(scenario())

    def test_cancelled_queued_job_closes_unstarted_operation(self):
        async def scenario():
            manager = JobManager(None)
            await manager._semaphore.acquire()
            async def operation():
                raise AssertionError("queued operation must not start")
            coroutine = operation()
            job = Job("queued", requires_network=True)
            task = asyncio.create_task(manager._execute(job, coroutine))
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            manager._semaphore.release()
            self.assertEqual(job.state, JobState.CANCELLED)
            self.assertIsNone(coroutine.cr_frame)
        asyncio.run(scenario())

    def test_member_csv_export_obeys_network_cooldown(self):
        class Runtime:
            def submit(self, coroutine):
                coroutine.close()
                return None
        manager = JobManager(Runtime())
        async def work():
            pass
        operation = work()
        job_id = manager.submit(operation, job_type="members.export_csv")
        self.assertTrue(manager.get(job_id).requires_network)
        operation.close()

    def test_remote_jobs_are_classified_for_cooldown(self):
        class Runtime:
            def submit(self, coroutine):
                coroutine.close()
                return None
        manager = JobManager(Runtime())
        async def work(): pass
        for operation, params, expected in (
            ("account.send_code", {}, True), ("account.status", {}, True),
            ("dialogs.sync", {}, True), ("messages.export", {"fetch_source": True}, True),
            ("members.export_csv", {}, True), ("messages.export", {"fetch_source": False}, False),
        ):
            coroutine = work()
            job_id = manager.submit(coroutine, job_type=operation, params=params)
            self.assertEqual(manager.get(job_id).requires_network, expected, operation)
            coroutine.close()

    def test_authorization_propagates_flood_wait(self):
        class Client:
            async def __call__(self, request):
                assert isinstance(request, functions.updates.GetStateRequest)
                raise errors.FloodWaitError(request=request, capture=30)
        with self.assertRaises(errors.FloodWaitError):
            asyncio.run(_user_authorized(Client()))

    def test_unauthorized_is_detected_without_new_code(self):
        class Client:
            async def __call__(self, request):
                raise errors.AuthKeyUnregisteredError(request=request)
        self.assertFalse(asyncio.run(_user_authorized(Client())))

    def test_different_account_never_becomes_active_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "db.sqlite")
            try:
                database.migrate()
                accounts = AccountRepository(database)
                first = AccountProfile(101, "a", "A", None, None)
                other = AccountProfile(202, "b", "B", None, None)
                accounts.save_profile(first)
                accounts.save_profile(first)
                with self.assertRaises(InvalidRequest):
                    accounts.save_profile(other)
                self.assertEqual(accounts.get_profile().telegram_id, 101)
                self.assertIsNone(accounts.local_id(202))
            finally:
                database.close()

    def test_message_fetch_rejects_changed_account_before_indexing(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, Mock
        from tg_observatory.services.message_service import MessageService
        gateway = SimpleNamespace(account_status=AsyncMock(return_value=SimpleNamespace(telegram_id=202)))
        accounts = Mock()
        service = MessageService(gateway, Mock(), accounts, Mock(), Mock())
        params = {"api_id": 1, "api_hash": "a" * 32, "dialog_id": 7, "expected_account_id": "101"}
        with self.assertRaises(InvalidRequest):
            asyncio.run(service.preview_range(params))
        accounts.save_profile.assert_not_called()

    def test_flood_wait_persists_and_blocks_next_remote_job_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cooldown.json"
            called = []
            async def scenario():
                manager = JobManager(None, cooldown_path=path)
                async def flood():
                    raise errors.FloodWaitError(request=None, capture=60)
                first = Job("first", requires_network=True)
                await manager._execute(first, flood())
                self.assertEqual(first.error["code"], "flood_wait")
                self.assertTrue(path.is_file())
                restarted = JobManager(None, cooldown_path=path)
                async def remote():
                    called.append("remote")
                second = Job("second", requires_network=True)
                await restarted._execute(second, remote())
                self.assertEqual(second.error["code"], "flood_wait")
                self.assertEqual(called, [])
                async def local():
                    called.append("local")
                third = Job("third", requires_network=False)
                await restarted._execute(third, local())
                self.assertEqual(third.state, JobState.COMPLETED)
                self.assertEqual(called, ["local"])
            asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
