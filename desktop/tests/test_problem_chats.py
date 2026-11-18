import unittest
from datetime import datetime, timedelta, timezone

from signal_archive import problem_chats


NOW = datetime(2026, 11, 17, tzinfo=timezone.utc)


class ProblemChatsTests(unittest.TestCase):
    def state(self):
        return {
            "problem_chat_days": 30,
            "dialogs": [
                {"id": "-100", "title": "Active group", "type": "group",
                 "last_message_date": "2026-11-16T12:00:00+00:00"},
                {"id": "-200", "title": "Quiet group", "type": "group",
                 "last_message_date": "2026-10-01T12:00:00+00:00"},
                {"id": "5", "title": "Silent bot", "type": "bot", "last_message_date": "", "history_known": True},
                {"id": "6", "title": "Zero chats", "type": "chat", "last_message_date": "", "history_known": True},
                {"id": "7", "title": "Old bot", "type": "bot",
                 "last_message_date": "2026-09-01T12:00:00+00:00"},
            ],
            "blocks": {"a": {"name": "Work", "chat_ids": ["-200"]}},
            "problem_chats": {
                "-300": {"title": "Pending group", "type": "group", "reason": "requested",
                         "message": "Ожидает одобрения."}
            },
        }

    def test_default_threshold_and_override(self):
        self.assertEqual(problem_chats.problem_days({}), 30)
        self.assertEqual(problem_chats.problem_days({"problem_chat_days": "7"}), 7)
        self.assertEqual(problem_chats.problem_days({"problem_chat_days": 0}), 1)

    def test_urgent_first_and_stale_second_without_duplicates(self):
        result = problem_chats.classify_problems(self.state(), now=NOW)
        self.assertEqual([item["title"] for item in result["urgent"]],
                         ["Pending group", "Silent bot", "Zero chats"])
        self.assertEqual([item["title"] for item in result["stale"]], ["Quiet group", "Old bot"])
        self.assertEqual(result["days"], 30)
        self.assertTrue(next(item for item in result["urgent"] if item["id"] == "-300")["selectable"])
        self.assertEqual(next(item for item in result["stale"] if item["id"] == "-200")["groups"], ["Work"])

    def test_threshold_changes_which_chats_are_stale(self):
        state = self.state()
        state["problem_chat_days"] = 60
        result = problem_chats.classify_problems(state, now=NOW)
        self.assertNotIn("Quiet group", [item["title"] for item in result["stale"]])
        self.assertIn("Old bot", [item["title"] for item in result["stale"]])

    def test_later_activity_resolves_a_stored_problem(self):
        state = self.state()
        state["dialogs"].append({"id": "-300", "title": "Pending group", "type": "group",
                                 "last_message_date": "2026-11-15T12:00:00+00:00"})
        result = problem_chats.classify_problems(state, now=NOW)
        self.assertNotIn("Pending group", [item["title"] for item in result["all"]])

    def test_record_and_clear_problem(self):
        state = {}
        problem_chats.record_problem(state, "-9", title="Test", kind="group",
                                     reason="problem", message="Failed")
        self.assertIn("-9", state["problem_chats"])
        self.assertTrue(problem_chats.clear_problem(state, "-9"))
        self.assertFalse(problem_chats.clear_problem(state, "-9"))

    def test_personal_chats_can_be_stale_and_outgoing_bot_is_urgent(self):
        state = {"dialogs": [
            {"id": "1", "title": "Old personal", "type": "chat", "last_message_date": "2026-01-01T00:00:00+00:00"},
            {"id": "2", "title": "Waiting bot", "type": "bot", "last_message_date": "2026-11-16T00:00:00+00:00", "last_message_out": True},
        ]}
        result = problem_chats.classify_problems(state, now=NOW)
        self.assertEqual([item["reason"] for item in result["urgent"]], ["bot_awaiting_reply"])
        self.assertEqual([item["title"] for item in result["stale"]], ["Old personal"])

    def test_links_are_normalized_and_untrusted_urls_are_rejected(self):
        state = {"dialogs": [
            {"id": "1", "title": "Quiet public", "type": "channel", "username": "news_test", "last_message_date": "2026-01-01T00:00:00+00:00"},
            {"id": "2", "title": "Quiet unsafe", "type": "group", "link": "https://evil.example/path", "last_message_date": "2026-01-01T00:00:00+00:00"},
        ], "problem_chats": {}}
        problem_chats.record_problem(state, "-300", title="Pending", kind="group", reason="requested", message="Waiting", reference="https://t.me/+JoinCode")
        result = problem_chats.classify_problems(state, now=NOW)
        links = {item["title"]: item["link"] for item in result["all"]}
        self.assertEqual(links["Quiet public"], "https://t.me/news_test")
        self.assertEqual(links["Quiet unsafe"], "")
        self.assertEqual(links["Pending"], "https://t.me/+JoinCode")
        self.assertTrue(next(item for item in result["all"] if item["title"] == "Pending")["pending_request"])

    def test_numeric_request_does_not_trigger_red_highlight(self):
        state = {"dialogs": [{"id": "-100", "title": "Group", "type": "group", "username": "group_test"}]}
        problem_chats.record_problem(state, "-100", title="Group", kind="group", reason="requested", message="Waiting", reference="-100")
        item = problem_chats.classify_problems(state, now=NOW)["all"][0]
        self.assertEqual(item["link"], "https://t.me/group_test")
        self.assertFalse(item["pending_request"])


if __name__ == "__main__":
    unittest.main()
