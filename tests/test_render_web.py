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
    """A single "AL" reply takes EVERY currently pending signal -- long and
    short alike -- not just one, per the user's 18 Sep 2026 request that
    simultaneous candidates should all be attempted rather than thrown
    away as ambiguous (existing per-trade limits, re-checked fresh inside
    approve_buy/approve_short for every candidate, naturally cap how many
    actually open)."""

    NOW_S = 1_700_000_000
    CONFIG = {"signal_confirmation_expiry_minutes": 10}
    LONG_SAVED = {"state": {"pending_buys": {"SOLUSDT": {"stop": 95}, "ETHUSDT": {"stop": 2400}},
                            "events": [{"type": "AL_ADAYI", "time": NOW_S * 1000 - 1000,
                                       "symbol": "SOLUSDT", "close": 100},
                                      {"type": "AL_ADAYI", "time": NOW_S * 1000 - 1000,
                                       "symbol": "ETHUSDT", "close": 2500}]}}
    SHORT_SAVED = {"state": {"pending_shorts": {"BNBUSDT": {"stop": 800, "leverage": 3}},
                             "events": [{"type": "SHORT_ADAYI", "time": NOW_S * 1000 - 1000,
                                        "symbol": "BNBUSDT", "close": 750}]}}
    EMPTY_SAVED = {"state": {}}

    def _app(self):
        return LiveApp(self.CONFIG, {}, {"TELEGRAM_CHAT_ID": "123"},
                       short_config=self.CONFIG, short_strategy_config={})

    def _payload(self, text="AL"):
        return {"update_id": 1, "message": {"text": text, "chat": {"id": 123, "type": "private"}}}

    def _frozen_clock(self):
        return patch("crypto_v1.render_web.time.time", return_value=self.NOW_S)

    def test_al_processes_every_pending_long_candidate_individually(self):
        app = self._app()
        with self._frozen_clock(), \
             patch("crypto_v1.render_web.fetch_runtime_state", return_value=self.LONG_SAVED), \
             patch("crypto_v1.render_web.fetch_runtime_state_short", return_value=self.EMPTY_SAVED), \
             patch.object(LiveApp, "_approve_long", return_value={"status": "ok"}) as long_mock, \
             patch.object(LiveApp, "_approve_short") as short_mock:
            result = app.telegram(self._payload())
        self.assertEqual(long_mock.call_count, 2)
        short_mock.assert_not_called()
        self.assertEqual(result["status"], "batch")
        self.assertEqual({r["symbol"] for r in result["results"]}, {"SOLUSDT", "ETHUSDT"})
        # Each call must see only its OWN candidate isolated, never both at once.
        seen = set()
        for call in long_mock.call_args_list:
            saved = call.args[1]
            events = saved["state"]["events"]
            self.assertEqual(len(events), 1)
            seen.add(events[0]["symbol"])
        self.assertEqual(seen, {"SOLUSDT", "ETHUSDT"})

    def test_al_processes_long_and_short_candidates_together(self):
        app = self._app()
        with self._frozen_clock(), \
             patch("crypto_v1.render_web.fetch_runtime_state", return_value=self.LONG_SAVED), \
             patch("crypto_v1.render_web.fetch_runtime_state_short", return_value=self.SHORT_SAVED), \
             patch.object(LiveApp, "_approve_long", return_value={"status": "ok"}) as long_mock, \
             patch.object(LiveApp, "_approve_short", return_value={"status": "ok"}) as short_mock:
            result = app.telegram(self._payload())
        self.assertEqual(long_mock.call_count, 2)
        self.assertEqual(short_mock.call_count, 1)
        self.assertEqual(len(result["results"]), 3)

    def test_al_rejects_when_nothing_is_pending(self):
        app = self._app()
        with self._frozen_clock(), \
             patch("crypto_v1.render_web.fetch_runtime_state", return_value=self.EMPTY_SAVED), \
             patch("crypto_v1.render_web.fetch_runtime_state_short", return_value=self.EMPTY_SAVED), \
             patch("crypto_v1.render_web.send_message"):
            result = app.telegram(self._payload())
        self.assertEqual(result, {"status": "rejected", "reason": "no_pending_signal"})

    def test_non_al_command_is_rejected_without_fetching_any_signal_state(self):
        app = self._app()
        with patch("crypto_v1.render_web.send_message") as send, \
             patch("crypto_v1.render_web.fetch_runtime_state") as fetch:
            result = app.telegram(self._payload("SHORT"))
        self.assertEqual(result, {"status": "rejected", "reason": "invalid_command"})
        fetch.assert_not_called()
        send.assert_called_once()
