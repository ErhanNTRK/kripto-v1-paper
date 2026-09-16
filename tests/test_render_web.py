import unittest
from unittest.mock import MagicMock
from crypto_v1.render_web import telegram_command, run_periodic_scans

class RenderWebTests(unittest.TestCase):
    def test_periodic_scans_calls_scan_each_iteration_and_survives_errors(self):
        app = MagicMock()
        app.scan.side_effect = [None, Exception('boom'), None]
        sleeps = []
        run_periodic_scans(app, interval_seconds=5, sleep=sleeps.append, max_iterations=3)
        self.assertEqual(app.scan.call_count, 3)
        self.assertEqual(sleeps, [5, 5, 5])

    def test_only_verified_private_chat_is_accepted(self):
        payload = {"update_id": 7, "message": {"text": " AL ", "chat": {"id": 123, "type": "private"}}}
        self.assertEqual(telegram_command(payload, "123"), {"update_id": 7, "command": "AL"})
        self.assertIsNone(telegram_command(payload, "999"))
        payload["message"]["chat"]["type"] = "group"
        self.assertIsNone(telegram_command(payload, "123"))
    def test_empty_message_is_ignored(self):
        payload = {"update_id": 7, "message": {"text": " ", "chat": {"id": 123, "type": "private"}}}
        self.assertIsNone(telegram_command(payload, "123"))
