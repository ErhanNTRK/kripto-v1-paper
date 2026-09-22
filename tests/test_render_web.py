import time
import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch
import crypto_v1.render_web as render_web
from crypto_v1.binance_trade import OrderRejected
from crypto_v1.render_web import LiveApp, _fill_pnl, telegram_command, run_periodic_scans

class ApproveLongWiringTests(unittest.TestCase):
    """_approve_long now opens a leveraged Futures long (21 Sep 2026),
    replacing unleveraged Spot for new entries -- it must call
    approve_long_leveraged with THIS app's short_config/futures_market/
    futures_executor (the wallet/config the short side already trades
    on), never the Spot config/market/executor. Using the wrong one
    would size/pay from the wrong wallet entirely."""

    CONFIG = {"signal_confirmation_expiry_minutes": 10}
    SHORT_CONFIG = {"signal_confirmation_expiry_minutes": 10, "risk_per_trade_usdt": 2.0}

    def _app(self):
        return LiveApp(self.CONFIG, {}, {"TELEGRAM_CHAT_ID": "123"},
                       short_config=self.SHORT_CONFIG, short_strategy_config={}, tag="2")

    def test_approve_long_uses_the_futures_wallet_and_config_not_spot(self):
        app = self._app()
        saved = {"state": {}}
        with patch("crypto_v1.render_web.approve_long_leveraged",
                  return_value={"status": "rejected", "reason": "no_pending_signal"}) as mock_approve:
            app._approve_long(7, saved, tag="2")
        mock_approve.assert_called_once()
        args = mock_approve.call_args.args
        self.assertEqual(args[3], saved)
        self.assertIs(args[4], app.short_config)
        self.assertIsNot(args[4], app.config)
        self.assertIs(args[6], app.futures_market)
        self.assertIs(args[7], app.futures_executor)

    def test_a_real_fill_sends_leverage_and_risk_in_the_aldim_message(self):
        app = self._app()
        plan = {"symbol": "ADAUSDT", "leverage": 2, "planned_loss_usdt": "1.00"}
        with patch("crypto_v1.render_web.approve_long_leveraged",
                  return_value={"status": "opened_and_protected", "plan": plan}), \
             patch("crypto_v1.render_web.send_message") as send:
            app._approve_long(7, {"state": {}}, tag="2")
        send.assert_called_once()
        message = send.call_args.args[0]
        self.assertIn("ADAUSDT", message)
        self.assertIn("2x", message)
        self.assertIn("1.00", message)


class RenderWebTests(unittest.TestCase):
    def test_periodic_scans_calls_tick_each_iteration_and_survives_errors(self):
        app = MagicMock()
        app.tick.side_effect = [None, Exception('boom'), None]
        sleeps = []
        run_periodic_scans(app, interval_seconds=5, sleep=sleeps.append, max_iterations=3)
        self.assertEqual(app.tick.call_count, 3)
        self.assertEqual(sleeps, [5, 5, 5])

    def test_periodic_scans_calls_detect_before_apps_each_iteration(self):
        # detect (github_worker.local_tick bound to a runtime dir, 21 Sep
        # 2026) must run BEFORE the apps tick, same thread/iteration, so
        # entries always see what detection just wrote -- never a stale or
        # concurrently-written file from a separate loop.
        app = MagicMock()
        order = []
        detect = MagicMock(side_effect=lambda: order.append('detect'))
        app.tick.side_effect = lambda: order.append('tick')
        run_periodic_scans(app, interval_seconds=1, sleep=lambda s: None, max_iterations=2, detect=detect)
        self.assertEqual(detect.call_count, 2)
        self.assertEqual(order, ['detect', 'tick', 'detect', 'tick'])

    def test_periodic_scans_survives_detect_failure_and_still_ticks_apps(self):
        app = MagicMock()
        detect = MagicMock(side_effect=Exception('boom'))
        run_periodic_scans(app, interval_seconds=1, sleep=lambda s: None, max_iterations=2, detect=detect)
        self.assertEqual(app.tick.call_count, 2)

    def test_periodic_scans_without_detect_is_unaffected(self):
        app = MagicMock()
        run_periodic_scans(app, interval_seconds=1, sleep=lambda s: None, max_iterations=2)
        self.assertEqual(app.tick.call_count, 2)

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

    def test_a_manual_al_tags_its_order_ids_like_auto_entry_does(self):
        # live_positions()/pilot_status() attribute an order to a system by
        # the digit right after the 5-char prefix; an untagged manual-AL
        # order belonged to neither app and escaped every exit scan.
        app = LiveApp(self.CONFIG, {}, {"TELEGRAM_CHAT_ID": "123"},
                      short_config=self.CONFIG, short_strategy_config={}, tag="2")
        with self._frozen_clock(), \
             patch("crypto_v1.render_web.fetch_runtime_state", return_value=self.LONG_SAVED), \
             patch("crypto_v1.render_web.fetch_runtime_state_short", return_value=self.SHORT_SAVED), \
             patch.object(LiveApp, "_approve_long", return_value={"status": "ok"}) as long_mock, \
             patch.object(LiveApp, "_approve_short", return_value={"status": "ok"}) as short_mock:
            app.telegram(self._payload())
        ids = [c.args[0] for c in long_mock.call_args_list] + [c.args[0] for c in short_mock.call_args_list]
        self.assertEqual(ids, [21000, 21001, 21002])

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


class LiveAppAutoEntryTests(unittest.TestCase):
    """Entries no longer wait for a Telegram "AL" reply, per the user's 18
    Sep 2026 instruction (he will not be at a computer to respond).
    auto_enter() processes up to LiveApp.ENTRIES_PER_TICK pending
    candidates per side per tick (not unbounded), keyed by the candidate's
    own signal timestamp so a repeat scan within the freshness window is
    idempotent (same client id, not a double-open). The cap exists because
    each approve_buy/approve_short call does its own fresh, heavy Binance
    account/orders scan (market.pilot_status()) -- taking every pending
    candidate in one tick multiplies that scan by the candidate count and
    live-tripped Binance's -1003 rate limit on 18 Sep 2026 when 5-6
    candidates were pending at once, which in turn crashed that same
    tick's exit check. Remaining candidates are picked up on later ticks,
    not lost (pending_buys/pending_shorts persist until filled or expired)."""

    NOW_S = 1_700_000_000
    CONFIG = {"signal_confirmation_expiry_minutes": 10}
    LONG_SAVED = {"state": {"pending_buys": {"SOLUSDT": {"stop": 95}},
                            "events": [{"type": "AL_ADAYI", "time": NOW_S * 1000 - 1000,
                                       "symbol": "SOLUSDT", "close": 100}]}}
    SHORT_SAVED = {"state": {"pending_shorts": {"BNBUSDT": {"stop": 800, "leverage": 3}},
                             "events": [{"type": "SHORT_ADAYI", "time": NOW_S * 1000 - 1000,
                                        "symbol": "BNBUSDT", "close": 750}]}}
    # Built without a class-body comprehension: comprehensions get their own
    # scope in Python 3 and cannot see NOW_S from the enclosing class body.
    MANY_LONG_SAVED = {"state": {
        "pending_buys": {"SOLUSDT": {"stop": 90}, "ETHUSDT": {"stop": 90}, "BNBUSDT": {"stop": 90}},
        "events": [{"type": "AL_ADAYI", "time": NOW_S * 1000 - 1000, "symbol": "SOLUSDT", "close": 100},
                  {"type": "AL_ADAYI", "time": NOW_S * 1000 - 1000, "symbol": "ETHUSDT", "close": 100},
                  {"type": "AL_ADAYI", "time": NOW_S * 1000 - 1000, "symbol": "BNBUSDT", "close": 100}]}}
    EMPTY_SAVED = {"state": {}}

    def _app(self):
        return LiveApp(self.CONFIG, {}, {"TELEGRAM_CHAT_ID": "123"},
                       short_config=self.CONFIG, short_strategy_config={})

    def _frozen_clock(self):
        return patch("crypto_v1.render_web.time.time", return_value=self.NOW_S)

    def test_auto_enter_executes_pending_long_and_short_without_any_telegram_command(self):
        app = self._app()
        with self._frozen_clock(), \
             patch("crypto_v1.render_web.fetch_runtime_state", return_value=self.LONG_SAVED), \
             patch("crypto_v1.render_web.fetch_runtime_state_short", return_value=self.SHORT_SAVED), \
             patch.object(LiveApp, "_approve_long", return_value={"status": "ok"}) as long_mock, \
             patch.object(LiveApp, "_approve_short", return_value={"status": "ok"}) as short_mock:
            result = app.auto_enter()
        self.assertEqual(long_mock.call_count, 1)
        self.assertEqual(short_mock.call_count, 1)
        self.assertEqual(result["status"], "auto_entry")
        # Keyed by the candidate's OWN timestamp, not a Telegram update_id.
        self.assertEqual(long_mock.call_args.args[0], self.NOW_S * 1000 - 1000)

    def test_auto_enter_caps_long_entries_at_entries_per_tick(self):
        app = self._app()
        with self._frozen_clock(), \
             patch("crypto_v1.render_web.fetch_runtime_state", return_value=self.MANY_LONG_SAVED), \
             patch("crypto_v1.render_web.fetch_runtime_state_short", return_value=self.EMPTY_SAVED), \
             patch.object(LiveApp, "_approve_long", return_value={"status": "ok"}) as long_mock:
            result = app.auto_enter()
        self.assertEqual(long_mock.call_count, LiveApp.ENTRIES_PER_TICK)
        self.assertEqual(len(result["results"]), LiveApp.ENTRIES_PER_TICK)

    def test_a_rejected_candidate_does_not_block_the_next_one_in_the_same_tick(self):
        # pending_candidates() is sorted by symbol (BNB, ETH, SOL). If BNB is
        # rejected before any order is placed (price drifted, limit hit),
        # the tick must move on to ETH instead of spending its whole budget
        # on the rejection -- otherwise a persistently-rejected symbol
        # starves every candidate sorted after it, tick after tick.
        app = self._app()
        with self._frozen_clock(), \
             patch("crypto_v1.render_web.fetch_runtime_state", return_value=self.MANY_LONG_SAVED), \
             patch("crypto_v1.render_web.fetch_runtime_state_short", return_value=self.EMPTY_SAVED), \
             patch.object(LiveApp, "_approve_long",
                          side_effect=[{"status": "rejected", "reason": "entry_price_moved"},
                                       {"status": "bought_and_protected"},
                                       {"status": "bought_and_protected"}]) as long_mock:
            result = app.auto_enter()
        self.assertEqual(long_mock.call_count, 1 + LiveApp.ENTRIES_PER_TICK)
        self.assertEqual([r["symbol"] for r in result["results"]], ["BNBUSDT", "ETHUSDT"])
        self.assertEqual(result["results"][1]["result"]["status"], "bought_and_protected")

    def test_a_failed_candidate_does_not_abort_the_rest_of_the_tick(self):
        # Live 21 Sep 2026: every first-ever buy attempt raised -1111 out of
        # _approve_long, the exception escaped auto_enter, and the other 16
        # pending candidates that tick were never tried -- /status showed
        # results: [] with no clue why. A failure is now a per-candidate
        # result, does not spend the ENTRIES_PER_TICK budget, and the loop
        # moves on to the next symbol.
        app = self._app()
        with self._frozen_clock(), \
             patch("crypto_v1.render_web.fetch_runtime_state", return_value=self.MANY_LONG_SAVED), \
             patch("crypto_v1.render_web.fetch_runtime_state_short", return_value=self.EMPTY_SAVED), \
             patch("crypto_v1.render_web.send_message") as send, \
             patch.object(LiveApp, "_approve_long",
                          side_effect=[OrderRejected(-1111),
                                       {"status": "bought_and_protected"},
                                       {"status": "bought_and_protected"}]) as long_mock:
            result = app.auto_enter()
        self.assertEqual(long_mock.call_count, 1 + LiveApp.ENTRIES_PER_TICK)
        self.assertEqual([r["symbol"] for r in result["results"]], ["BNBUSDT", "ETHUSDT"])
        self.assertEqual(result["results"][0]["result"]["status"], "failed")
        self.assertEqual(result["results"][0]["result"]["code"], -1111)
        self.assertEqual(result["results"][1]["result"]["status"], "bought_and_protected")
        # A plain Binance rejection (nothing placed) is not worth a Telegram alert.
        send.assert_not_called()

    def test_a_planning_value_error_is_a_plain_rejection_without_alert(self):
        # protective_order_plan raises ValueError when the pilot capital can
        # no longer fund a minimum-size order; with many candidates pending
        # that recurs every tick and must not page anyone or spend budget.
        app = self._app()
        with self._frozen_clock(), \
             patch("crypto_v1.render_web.fetch_runtime_state", return_value=self.MANY_LONG_SAVED), \
             patch("crypto_v1.render_web.fetch_runtime_state_short", return_value=self.EMPTY_SAVED), \
             patch("crypto_v1.render_web.send_message") as send, \
             patch.object(LiveApp, "_approve_long",
                          side_effect=[ValueError("order is below Binance minimums"),
                                       {"status": "bought_and_protected"},
                                       {"status": "bought_and_protected"}]) as long_mock:
            result = app.auto_enter()
        self.assertEqual(long_mock.call_count, 2)
        self.assertEqual(result["results"][0]["result"],
                         {"status": "rejected", "reason": "order is below Binance minimums"})
        self.assertEqual(result["results"][1]["result"]["status"], "bought_and_protected")
        send.assert_not_called()

    def test_a_non_rejection_failure_alerts_telegram_and_continues(self):
        app = self._app()
        with self._frozen_clock(), \
             patch("crypto_v1.render_web.fetch_runtime_state", return_value=self.MANY_LONG_SAVED), \
             patch("crypto_v1.render_web.fetch_runtime_state_short", return_value=self.EMPTY_SAVED), \
             patch("crypto_v1.render_web.send_message") as send, \
             patch.object(LiveApp, "_approve_long",
                          side_effect=[RuntimeError("buy was not fully filled; operator review required"),
                                       {"status": "bought_and_protected"},
                                       {"status": "bought_and_protected"}]) as long_mock:
            result = app.auto_enter()
        self.assertEqual(long_mock.call_count, 2)
        self.assertEqual(result["results"][0]["result"]["status"], "failed")
        send.assert_called_once()
        self.assertIn("BNBUSDT", send.call_args.args[0])

    def test_auto_enter_is_a_noop_with_nothing_pending(self):
        app = self._app()
        with self._frozen_clock(), \
             patch("crypto_v1.render_web.fetch_runtime_state", return_value=self.EMPTY_SAVED), \
             patch("crypto_v1.render_web.fetch_runtime_state_short", return_value=self.EMPTY_SAVED):
            result = app.auto_enter()
        self.assertEqual(result, {"status": "auto_entry", "results": []})

    def test_tick_calls_auto_enter_then_scan_and_survives_entry_errors(self):
        app = self._app()
        with patch.object(LiveApp, "auto_enter", side_effect=RuntimeError("boom")) as enter_mock, \
             patch.object(LiveApp, "scan", return_value={"status": "scanned", "results": []}) as scan_mock:
            result = app.tick()
        enter_mock.assert_called_once()
        scan_mock.assert_called_once()
        self.assertEqual(result["exits"]["status"], "scanned")


class ScanResilienceTests(unittest.TestCase):
    """A Binance error on one market (spot or futures) must never suppress
    the exit check on the other -- exits are safety-critical and must
    keep running. Live-tripped 18 Sep 2026 when a -1003 rate limit during
    the spot side's live_positions() call propagated out of scan()
    entirely, leaving the futures side (and the whole /scan HTTP request)
    unchecked that tick."""

    CONFIG = {"signal_confirmation_expiry_minutes": 10, "trailing_atr": 2.0}

    def _app(self):
        app = LiveApp(self.CONFIG, {"trailing_atr": 2.0}, {"TELEGRAM_CHAT_ID": "123"},
                     short_config=self.CONFIG, short_strategy_config={})
        return app

    def test_spot_failure_does_not_prevent_futures_exit_check(self):
        app = self._app()
        app.market.live_positions = MagicMock(side_effect=RuntimeError("Binance rejected order request: -1003"))
        app.futures_market.live_positions = MagicMock(return_value=[])
        result = app.scan()
        self.assertEqual(result["status"], "scanned")
        self.assertTrue(any(r.get("status") == "scan_failed" and r.get("side") == "spot" for r in result["results"]))
        app.futures_market.live_positions.assert_called_once()

    def test_futures_failure_does_not_prevent_spot_result(self):
        app = self._app()
        app.market.live_positions = MagicMock(return_value=[])
        app.futures_market.live_positions = MagicMock(side_effect=RuntimeError("boom"))
        result = app.scan()
        self.assertEqual(result["status"], "scanned")
        self.assertTrue(any(r.get("status") == "scan_failed" and r.get("side") == "futures" for r in result["results"]))

    def test_scan_analyzes_positions_on_the_apps_own_interval_not_always_4h(self):
        # Bug found live 21 Sep 2026: scan() hardcoded FOUR_HOUR for every
        # app's exit check. A position the 2H system opened mid-way through
        # the current, still-open 4H bar had its buy_time floored to that
        # bar's start, which has no closed 4H candle yet -- analysis()'s
        # max() over an empty filtered list crashed with "Spot exit scan
        # failed: max() iterable argument is empty", silently skipping the
        # exit check for a real, just-opened position every single tick.
        app = LiveApp(self.CONFIG, {"trailing_atr": 2.0}, {"TELEGRAM_CHAT_ID": "123"},
                     short_config=self.CONFIG, short_strategy_config={}, tag="2", interval=render_web.TWO_HOUR)
        position = {"symbol": "ADAUSDT", "buy_time": 0}
        short_position = {"symbol": "BNBUSDT", "side": "short", "open_time": 0}
        app.market.live_positions = MagicMock(return_value=[position])
        app.market.analysis = MagicMock(return_value=({"c": 1}, {"c": 1}, 1))
        app.futures_market.live_positions = MagicMock(return_value=[short_position])
        app.futures_market.analysis = MagicMock(return_value=({"c": 1}, {"c": 1}, 1))
        with patch("crypto_v1.render_web.exit_decision", return_value=None), \
             patch("crypto_v1.render_web.short_exit_decision", return_value=None):
            app.scan()
        self.assertEqual(app.market.analysis.call_args.args[2], render_web.TWO_HOUR)
        self.assertEqual(app.futures_market.analysis.call_args.args[2], render_web.TWO_HOUR)

    def test_default_interval_stays_four_hour_for_the_4h_app(self):
        app = self._app()
        self.assertEqual(app.interval, render_web.FOUR_HOUR)

    def test_long_futures_exit_forces_cap_at_target_false(self):
        # short_live_config.json has no "cap_at_target" key at all, so
        # exit_decision's own default (True, "capped") would silently
        # diverge from the walk-forward-validated "let winners run"
        # behavior unless scan() explicitly overrides it for the leveraged
        # long exit path.
        app = LiveApp(self.CONFIG, {"trailing_atr": 2.0}, {"TELEGRAM_CHAT_ID": "123"},
                     short_config=self.CONFIG, short_strategy_config={"trailing_atr": 2.0})
        long_position = {"symbol": "ADAUSDT", "side": "long", "open_time": 0}
        app.market.live_positions = MagicMock(return_value=[])
        app.futures_market.live_positions = MagicMock(return_value=[long_position])
        app.futures_market.analysis = MagicMock(return_value=({"c": 1}, {"c": 1}, 1))
        with patch("crypto_v1.render_web.exit_decision", return_value=None) as exit_decision_mock:
            app.scan()
        exit_decision_mock.assert_called_once()
        passed_config = exit_decision_mock.call_args.args[4]
        self.assertIs(passed_config["cap_at_target"], False)

    # BTC merely above EMA20 (a valid very_loose entry) but NOT in a full
    # EMA20>50>200 stack: btc_up_ok passes for "very_loose", fails for
    # "strict". Price is above long_exit and above entry, no trailing
    # trigger (high < entry + risk) -- so the ONLY thing that could force
    # an exit is the btc filter running in the wrong mode.
    LOOSE_ONLY_BTC = {"c": 100, "ema20": 99, "ema50": 101, "ema200": 90}
    HELD_FEATURE = {"c": 101, "atr": 1, "long_exit": 98}
    VERY_LOOSE_STRATEGY = {"trailing_atr": 2.0, "btc_filter": "very_loose"}

    def _app_with_strategy(self):
        # Spot reads cap_at_target from its own live_config.json (false there);
        # the futures long path forces it False itself.
        return LiveApp(dict(self.CONFIG, cap_at_target=False), self.VERY_LOOSE_STRATEGY,
                       {"TELEGRAM_CHAT_ID": "123"},
                       short_config=self.CONFIG, short_strategy_config=self.VERY_LOOSE_STRATEGY)

    def test_leveraged_long_exit_uses_the_entry_btc_filter_mode_not_strict(self):
        # Found in the 22 Sep 2026 review, before any leveraged long had
        # gone through this path: exit_decision was given the bare
        # ShortWindowLongModel.sell, which it calls as sell_fn(feature,
        # btc) -- config=None -> long_exit's "strict" default -> the
        # position opened under very_loose would be force-closed on the
        # first scan after entry (research_v5.long_exit's own warning).
        app = self._app_with_strategy()
        app.market.live_positions = MagicMock(return_value=[])
        app.futures_market.live_positions = MagicMock(return_value=[
            {"symbol": "ADAUSDT", "side": "long", "entry": 100, "stop_price": 95,
             "quantity": "1", "stop_client_id": "kv1fq4x", "open_time": 0}])
        app.futures_market.analysis = MagicMock(return_value=(self.HELD_FEATURE, self.LOOSE_ONLY_BTC, 100))
        result = app.scan()
        self.assertEqual(result["results"], [])

    def test_spot_long_exit_uses_the_entry_btc_filter_mode_not_strict(self):
        app = self._app_with_strategy()
        app.futures_market.live_positions = MagicMock(return_value=[])
        app.market.live_positions = MagicMock(return_value=[
            {"symbol": "ADAUSDT", "entry": 100, "stop_price": 95,
             "quantity": "1", "stop_client_id": "kv1s4x", "buy_time": 0}])
        app.market.analysis = MagicMock(return_value=(self.HELD_FEATURE, self.LOOSE_ONLY_BTC, 100))
        result = app.scan()
        self.assertEqual(result["results"], [])

    def test_leveraged_long_still_exits_when_the_very_loose_filter_itself_fails(self):
        # Guards the fix above from having simply disabled the trend exit.
        app = self._app_with_strategy()
        app.market.live_positions = MagicMock(return_value=[])
        app.futures_market.live_positions = MagicMock(return_value=[
            {"symbol": "ADAUSDT", "side": "long", "entry": 100, "stop_price": 95,
             "quantity": "1", "stop_client_id": "kv1fq4x", "open_time": 0}])
        btc_below_ema20 = dict(self.LOOSE_ONLY_BTC, c=98)
        app.futures_market.analysis = MagicMock(return_value=(self.HELD_FEATURE, btc_below_ema20, 100))
        result = app.scan()
        self.assertEqual(result["results"][0]["status"], "preview_exit")
        self.assertEqual(result["results"][0]["reason"], "profit_signal")


class RateLimitCircuitBreakerTests(unittest.TestCase):
    """A real Binance -1003 (too many requests) must stop this process from
    hitting Binance at all for a cooldown window -- retrying every 5
    minutes while banned achieves nothing (every call fails anyway) and
    risks the ban treating each attempt as a further violation. Live-
    observed 18 Sep 2026: a burst of pilot_status() calls (one per pending
    candidate, since fixed by ENTRIES_PER_TICK) tripped -1003 and it
    persisted across several 5-minute ticks afterward."""

    CONFIG = {"signal_confirmation_expiry_minutes": 10, "trailing_atr": 2.0}

    def setUp(self):
        self._saved = (render_web._rate_limited_until,
                       render_web._consecutive_rate_limit_hits)
        render_web._rate_limited_until = 0.0
        render_web._consecutive_rate_limit_hits = 0

    def tearDown(self):
        (render_web._rate_limited_until,
        render_web._consecutive_rate_limit_hits) = self._saved

    def _app(self):
        return LiveApp(self.CONFIG, {"trailing_atr": 2.0}, {"TELEGRAM_CHAT_ID": "123"},
                       short_config=self.CONFIG, short_strategy_config={})

    def test_a_1003_rejection_starts_a_cooldown_and_the_next_tick_skips_entirely(self):
        app = self._app()
        app.market.live_positions = MagicMock(side_effect=OrderRejected(-1003))
        app.futures_market.live_positions = MagicMock(return_value=[])
        with patch.object(LiveApp, "auto_enter", return_value={"status": "auto_entry", "results": []}):
            app.tick()
        self.assertTrue(render_web._rate_limited())
        with patch.object(LiveApp, "auto_enter") as enter_mock, \
             patch.object(LiveApp, "scan") as scan_mock:
            result = app.tick()
        self.assertEqual(result["status"], "cooling_down")
        enter_mock.assert_not_called()
        scan_mock.assert_not_called()

    def test_a_different_rejection_code_does_not_start_a_cooldown(self):
        app = self._app()
        app.market.live_positions = MagicMock(side_effect=OrderRejected(-2011))
        app.futures_market.live_positions = MagicMock(return_value=[])
        with patch.object(LiveApp, "auto_enter", return_value={"status": "auto_entry", "results": []}):
            app.tick()
        self.assertFalse(render_web._rate_limited())

    def test_an_unrelated_exception_does_not_start_a_cooldown(self):
        app = self._app()
        app.market.live_positions = MagicMock(side_effect=RuntimeError("network blip"))
        app.futures_market.live_positions = MagicMock(return_value=[])
        with patch.object(LiveApp, "auto_enter", return_value={"status": "auto_entry", "results": []}):
            app.tick()
        self.assertFalse(render_web._rate_limited())

    def test_cooldown_doubles_on_each_consecutive_hit_within_the_same_incident(self):
        # Live-observed 18 Sep 2026: a fixed 5-minute cooldown kept getting
        # re-hit by -1003 every 5 minutes for 25+ minutes straight against a
        # longer real ban. Consecutive hits (close enough together to be the
        # same incident) must back off further each time instead of probing
        # at the same fixed interval forever.
        render_web._note_if_rate_limited(OrderRejected(-1003))
        first_cooldown = render_web._rate_limited_until - time.time()
        self.assertAlmostEqual(first_cooldown, render_web._RATE_LIMIT_BASE_COOLDOWN_S, delta=2)
        render_web._note_if_rate_limited(OrderRejected(-1003))
        second_cooldown = render_web._rate_limited_until - time.time()
        self.assertAlmostEqual(second_cooldown, render_web._RATE_LIMIT_BASE_COOLDOWN_S * 2, delta=2)
        render_web._note_if_rate_limited(OrderRejected(-1003))
        third_cooldown = render_web._rate_limited_until - time.time()
        self.assertAlmostEqual(third_cooldown, render_web._RATE_LIMIT_BASE_COOLDOWN_S * 4, delta=2)

    def test_cooldown_is_capped_at_the_configured_maximum(self):
        for _ in range(10):
            render_web._note_if_rate_limited(OrderRejected(-1003))
        cooldown = render_web._rate_limited_until - time.time()
        self.assertAlmostEqual(cooldown, render_web._RATE_LIMIT_MAX_COOLDOWN_S, delta=2)

    def test_escalation_keeps_climbing_across_ticks_spaced_by_the_cooldown_itself(self):
        # Regression (18 Sep 2026): an earlier version reset the escalation
        # whenever the gap since the last hit exceeded 2x the BASE cooldown.
        # But a tick only ever runs again right as the previous cooldown
        # expires, so during a real ongoing ban the gap between hits is
        # always approximately equal to that previous cooldown -- once the
        # cooldown itself grew past the reset threshold (at the 2x/10-minute
        # step), every subsequent hit was wrongly treated as a "fresh"
        # incident and the escalation got stuck oscillating at 2x forever,
        # never reaching a longer, more appropriate backoff for a long ban.
        app = self._app()
        app.futures_market.live_positions = MagicMock(return_value=[])

        def hit_tick():
            app.market.live_positions = MagicMock(side_effect=OrderRejected(-1003))
            with patch.object(LiveApp, "auto_enter", return_value={"status": "auto_entry", "results": []}):
                app.tick()
            return render_web._consecutive_rate_limit_hits

        self.assertEqual(hit_tick(), 1)
        render_web._rate_limited_until = 0.0  # simulate the cooldown having elapsed
        self.assertEqual(hit_tick(), 2)
        render_web._rate_limited_until = 0.0
        self.assertEqual(hit_tick(), 3)
        render_web._rate_limited_until = 0.0
        self.assertEqual(hit_tick(), 4)

    def test_a_tick_that_completes_cleanly_resets_the_escalation(self):
        app = self._app()
        app.futures_market.live_positions = MagicMock(return_value=[])
        app.market.live_positions = MagicMock(side_effect=OrderRejected(-1003))
        with patch.object(LiveApp, "auto_enter", return_value={"status": "auto_entry", "results": []}):
            app.tick()
        self.assertEqual(render_web._consecutive_rate_limit_hits, 1)
        render_web._rate_limited_until = 0.0
        app.market.live_positions = MagicMock(return_value=[])  # Binance answering normally again
        with patch.object(LiveApp, "auto_enter", return_value={"status": "auto_entry", "results": []}):
            app.tick()
        self.assertEqual(render_web._consecutive_rate_limit_hits, 0)


class FillPnlTests(unittest.TestCase):
    def test_long_pnl_is_proceeds_minus_entry_cost(self):
        order = {"executedQty": "2", "cummulativeQuoteQty": "250"}
        self.assertEqual(_fill_pnl(entry="100", order=order, side="long"), 50)

    def test_short_pnl_is_entry_value_minus_buyback_cost(self):
        order = {"executedQty": "2", "cummulativeQuoteQty": "150"}
        self.assertEqual(_fill_pnl(entry="100", order=order, side="short"), 50)

    def test_futures_fill_reports_its_quote_total_as_cumquote(self):
        # /fapi order responses use cumQuote, not Spot's cummulativeQuoteQty;
        # reading only the Spot name meant no futures close ever showed PNL.
        self.assertEqual(_fill_pnl(entry="100", order={"executedQty": "2", "cumQuote": "250"}, side="long"), 50)
        self.assertEqual(_fill_pnl(entry="100", order={"executedQty": "2", "cumQuote": "150"}, side="short"), 50)

    def test_no_order_or_empty_fill_yields_no_pnl(self):
        self.assertIsNone(_fill_pnl(entry="100", order=None, side="long"))
        self.assertIsNone(_fill_pnl(entry="100", order={"executedQty": "0", "cummulativeQuoteQty": "0"}, side="long"))


class TickConcurrencyAndStatusTests(unittest.TestCase):
    """The periodic thread and an external /scan can tick the same app at
    the same instant; the late caller must report busy rather than run a
    second, concurrent tick (a duplicate market buy is possible in that
    window, since Binance only enforces client-id uniqueness among OPEN
    orders). Every tick outcome is recorded on the app for /status."""

    CONFIG = {"signal_confirmation_expiry_minutes": 10}

    def _app(self, tag=""):
        return LiveApp(self.CONFIG, {}, {"TELEGRAM_CHAT_ID": "123"},
                       short_config=self.CONFIG, short_strategy_config={}, tag=tag)

    def test_a_concurrent_tick_reports_busy_instead_of_running(self):
        app = self._app()
        app._tick_lock.acquire()  # simulate the periodic thread mid-tick
        try:
            with patch.object(LiveApp, "auto_enter") as enter_mock, \
                 patch.object(LiveApp, "scan") as scan_mock:
                result = app.tick()
        finally:
            app._tick_lock.release()
        self.assertEqual(result["status"], "busy")
        enter_mock.assert_not_called()
        scan_mock.assert_not_called()

    def test_tick_records_its_result_and_releases_the_lock(self):
        app = self._app()
        with patch.object(LiveApp, "auto_enter", return_value={"status": "auto_entry", "results": []}), \
             patch.object(LiveApp, "scan", return_value={"status": "scanned", "results": []}):
            app.tick()
            second = app.tick()  # lock released: a later tick runs normally
        self.assertEqual(app.last_tick["result"]["exits"]["status"], "scanned")
        self.assertEqual(second["exits"]["status"], "scanned")

    def test_status_snapshot_reports_switch_breaker_and_last_ticks_without_ticking(self):
        four_h, two_h = self._app("4"), self._app("2")
        four_h.last_tick = {"at": 1.0, "result": {"entries": {"results": [
            {"symbol": "SOLUSDT", "side": "long", "result": {"status": "rejected", "reason": "entry_price_moved"}}]}}}
        with patch.dict(render_web.STATUS, {"orders_enabled": True}), \
             patch.object(render_web, "_rate_limited_until", 0.0), \
             patch.object(render_web, "_consecutive_rate_limit_hits", 0), \
             patch.object(LiveApp, "tick") as tick_mock:
            snapshot = render_web.status_snapshot([four_h, two_h], now=lambda: 100.0)
        tick_mock.assert_not_called()
        self.assertTrue(snapshot["orders_enabled"])
        self.assertEqual(snapshot["rate_limit"], {"cooling_down": False, "cooldown_ends_in_s": 0,
                                                  "consecutive_hits": 0, "last_message": ""})
        self.assertEqual(snapshot["apps"]["4"]["result"]["entries"]["results"][0]["result"]["reason"],
                         "entry_price_moved")
        self.assertIsNone(snapshot["apps"]["2"])

    def test_status_snapshot_shows_an_active_cooldown(self):
        with patch.object(render_web, "_rate_limited_until", 400.0), \
             patch.object(render_web, "_consecutive_rate_limit_hits", 2), \
             patch.object(render_web, "_rate_limit_last_message", "HTTP 418: banned"):
            snapshot = render_web.status_snapshot([], now=lambda: 100.0)
        self.assertEqual(snapshot["rate_limit"], {"cooling_down": True, "cooldown_ends_in_s": 300,
                                                  "consecutive_hits": 2, "last_message": "HTTP 418: banned"})

    def test_positions_snapshot_reports_real_quantity_stop_and_unrealized_pnl(self):
        # Added 21 Sep 2026, the first day real fills existed: how much did
        # it actually buy, where is the stop, what is it worth right now.
        # entry/stop/quantity come from live_positions() (the exchange-
        # native fill and protective stop), price from a fresh ticker read.
        app = self._app("4")
        app.market = MagicMock()
        app.market.live_positions.return_value = [
            {"symbol": "ADAUSDT", "entry": Decimal("0.50"), "stop_price": Decimal("0.47"),
             "quantity": "100", "stop_client_id": "kv1s4x", "buy_time": 0}]
        app.market.price.return_value = Decimal("0.55")
        app.futures_market = None
        snapshot = render_web.positions_snapshot([app])
        position = snapshot["4"]["spot"][0]
        self.assertEqual(position["symbol"], "ADAUSDT")
        self.assertEqual(position["side"], "long")
        self.assertEqual(Decimal(position["cost_usdt"]), Decimal("50"))
        self.assertEqual(Decimal(position["current_value_usdt"]), Decimal("55"))
        self.assertEqual(Decimal(position["unrealized_pnl_usdt"]), Decimal("5"))
        self.assertEqual(position["unrealized_pnl_pct"], "10.00")
        self.assertEqual(snapshot["4"]["futures"], [])

    def test_positions_snapshot_flips_pnl_sign_for_a_short(self):
        # A short is up when price FALLS below entry -- the opposite of a
        # long -- and its stop sits ABOVE current price, not below.
        app = self._app("2")
        app.market = MagicMock(live_positions=MagicMock(return_value=[]))
        app.futures_market = MagicMock()
        app.futures_market.live_positions.return_value = [
            {"symbol": "BNBUSDT", "entry": Decimal("800"), "stop_price": Decimal("840"),
             "quantity": "1", "stop_client_id": "kv1fp2x", "open_time": 0}]
        app.futures_market.price.return_value = Decimal("760")
        snapshot = render_web.positions_snapshot([app])
        position = snapshot["2"]["futures"][0]
        self.assertEqual(position["side"], "short")
        self.assertEqual(Decimal(position["unrealized_pnl_usdt"]), Decimal("40"))
        self.assertEqual(position["unrealized_pnl_pct"], "5.00")

    def test_positions_snapshot_keeps_a_leveraged_futures_long_as_a_long(self):
        # Every futures position was hardcoded "short" (from before the
        # leveraged long existed): a long up 10% showed as a short down 10%.
        app = self._app("4")
        app.market = MagicMock(live_positions=MagicMock(return_value=[]))
        app.futures_market = MagicMock()
        app.futures_market.live_positions.return_value = [
            {"symbol": "ADAUSDT", "side": "long", "entry": Decimal("100"), "stop_price": Decimal("95"),
             "quantity": "1", "stop_client_id": "kv1fq4x", "open_time": 0}]
        app.futures_market.price.return_value = Decimal("110")
        position = render_web.positions_snapshot([app])["4"]["futures"][0]
        self.assertEqual(position["side"], "long")
        self.assertEqual(Decimal(position["unrealized_pnl_usdt"]), Decimal("10"))
        self.assertEqual(position["distance_to_stop_pct"], "13.64")

    def test_an_ip_ban_pauses_until_binances_own_ban_clock_not_just_the_cooldown(self):
        # HTTP 418's msg carries the exact end of the ban (epoch ms). A first
        # hit's 5-minute cooldown would probe again inside the ban and extend
        # it, so the pause must run to the later of the two clocks.
        now = 1_000_000.0
        banned_until_ms = int((now + 3 * 3600) * 1000)  # a 3-hour ban
        msg = f"Way too much request weight used; IP banned until {banned_until_ms}. Please use WebSocket Streams."
        with patch.object(render_web, "_rate_limited_until", 0.0), \
             patch.object(render_web, "_consecutive_rate_limit_hits", 0), \
             patch.object(render_web, "_rate_limit_last_message", ""), \
             patch("crypto_v1.render_web.time.time", return_value=now):
            render_web._note_if_rate_limited(OrderRejected(-1003, msg, 418))
            self.assertGreaterEqual(render_web._rate_limited_until, now + 3 * 3600)
            self.assertEqual(render_web._consecutive_rate_limit_hits, 1)
            self.assertIn("HTTP 418", render_web._rate_limit_last_message)
            # A plain 429 with no ban clock keeps the short first-tier cooldown.
            render_web._consecutive_rate_limit_hits = 0
            render_web._note_if_rate_limited(OrderRejected(-1003, "Too much request weight used", 429))
            self.assertEqual(render_web._rate_limited_until, now + 300)
