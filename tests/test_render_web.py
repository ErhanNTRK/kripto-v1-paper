import unittest
from unittest.mock import MagicMock, patch
from crypto_v1.render_web import LiveApp, telegram_command, run_periodic_scans

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


class LiveAppTelegramRoutingTests(unittest.TestCase):
    """A single "AL" reply must resolve to whichever ONE side (long or
    short) actually has a pending signal, per the user's 18 Sep 2026
    preference for one consistent reply word -- never guess between two
    simultaneously pending candidates, and never silently do nothing when
    the reply can't be resolved."""

    def _app(self):
        return LiveApp({}, {}, {"TELEGRAM_CHAT_ID": "123"}, short_config={}, short_strategy_config={})

    def _payload(self, text="AL"):
        return {"update_id": 1, "message": {"text": text, "chat": {"id": 123, "type": "private"}}}

    def test_al_routes_to_long_when_only_long_is_pending(self):
        app = self._app()
        with patch("crypto_v1.render_web.pending_candidates", return_value=[{"symbol": "SOLUSDT"}]), \
             patch("crypto_v1.render_web.pending_short_candidates", return_value=[]), \
             patch("crypto_v1.render_web.fetch_runtime_state", return_value={}), \
             patch("crypto_v1.render_web.fetch_runtime_state_short", return_value={}), \
             patch.object(LiveApp, "_approve_long", return_value={"status": "ok"}) as long_mock, \
             patch.object(LiveApp, "_approve_short") as short_mock:
            result = app.telegram(self._payload())
        long_mock.assert_called_once()
        short_mock.assert_not_called()
        self.assertEqual(result, {"status": "ok"})

    def test_al_routes_to_short_when_only_short_is_pending(self):
        app = self._app()
        with patch("crypto_v1.render_web.pending_candidates", return_value=[]), \
             patch("crypto_v1.render_web.pending_short_candidates", return_value=[{"symbol": "SOLUSDT"}]), \
             patch("crypto_v1.render_web.fetch_runtime_state", return_value={}), \
             patch("crypto_v1.render_web.fetch_runtime_state_short", return_value={}), \
             patch.object(LiveApp, "_approve_long") as long_mock, \
             patch.object(LiveApp, "_approve_short", return_value={"status": "ok"}) as short_mock:
            result = app.telegram(self._payload())
        short_mock.assert_called_once()
        long_mock.assert_not_called()
        self.assertEqual(result, {"status": "ok"})

    def test_al_rejects_as_ambiguous_when_both_sides_have_a_pending_signal(self):
        app = self._app()
        with patch("crypto_v1.render_web.pending_candidates", return_value=[{"symbol": "A"}]), \
             patch("crypto_v1.render_web.pending_short_candidates", return_value=[{"symbol": "B"}]), \
             patch("crypto_v1.render_web.fetch_runtime_state", return_value={}), \
             patch("crypto_v1.render_web.fetch_runtime_state_short", return_value={}), \
             patch("crypto_v1.render_web.send_message"), \
             patch.object(LiveApp, "_approve_long") as long_mock, \
             patch.object(LiveApp, "_approve_short") as short_mock:
            result = app.telegram(self._payload())
        self.assertEqual(result, {"status": "rejected", "reason": "ambiguous_pending_signals"})
        long_mock.assert_not_called()
        short_mock.assert_not_called()

    def test_al_rejects_when_nothing_is_pending(self):
        app = self._app()
        with patch("crypto_v1.render_web.pending_candidates", return_value=[]), \
             patch("crypto_v1.render_web.pending_short_candidates", return_value=[]), \
             patch("crypto_v1.render_web.fetch_runtime_state", return_value={}), \
             patch("crypto_v1.render_web.fetch_runtime_state_short", return_value={}), \
             patch("crypto_v1.render_web.send_message"):
            result = app.telegram(self._payload())
        self.assertEqual(result, {"status": "rejected", "reason": "no_pending_signal"})

    def test_non_al_command_is_rejected_without_checking_any_signal(self):
        app = self._app()
        with patch("crypto_v1.render_web.send_message") as send, \
             patch("crypto_v1.render_web.pending_candidates") as pc:
            result = app.telegram(self._payload("SHORT"))
        self.assertEqual(result, {"status": "rejected", "reason": "invalid_command"})
        pc.assert_not_called()
        send.assert_called_once()
