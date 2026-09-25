import unittest
from unittest.mock import Mock, call
from crypto_v1.binance_trade import OrderRejected, OrderStateUnknown
from crypto_v1.live_short_monitor import execute_long_futures_exit, short_exit_decision, execute_short_exit

P = {"symbol": "SOLUSDT", "entry": 100, "stop_price": 105, "quantity": "0.1", "stop_client_id": "kv1fp7"}
F = {"c": 90, "atr": 2, "short_exit": 150}
DOWN_BTC = {"c": 100, "ema20": 102, "ema50": 104, "ema200": 110}
# loose-down-ok (c<ema50: 100<101) but NOT strict-down-ok (c<ema20 fails: 100<99 is false)
CHOPPY_DOWN = {"c": 100, "ema20": 99, "ema50": 101, "ema200": 110}
C = {"trailing_atr": 2}


class ShortExitDecisionTests(unittest.TestCase):
    def test_trend_exit_when_short_exit_level_is_broken_and_btc_still_down(self):
        broken = dict(F, c=96, short_exit=95)
        self.assertEqual(short_exit_decision(P, broken, DOWN_BTC, 96, C), "profit_signal")

    def test_no_exit_while_short_exit_level_holds_and_no_trailing_trigger(self):
        held = dict(F, c=96, short_exit=98)
        self.assertIsNone(short_exit_decision(P, held, DOWN_BTC, 96, C))

    def test_emergency_risk_when_invalid_stop_geometry(self):
        bad = dict(P, stop_price=99)  # stop must be ABOVE entry for a short
        self.assertEqual(short_exit_decision(bad, F, DOWN_BTC, 89, C), "emergency_risk")

    def test_trailing_profit_triggers_after_price_moves_favorably_then_bounces(self):
        # entry=100, stop=105 -> risk=5, entry-risk=95; low=90 (<=95) opens
        # the trailing check; trailing = 90 + trailing_atr(2)*atr(2) = 94.
        no_trigger = dict(F, c=93)  # 93 < 94: not yet
        self.assertIsNone(short_exit_decision(P, no_trigger, DOWN_BTC, 90, C))
        trigger = dict(F, c=95)  # 95 >= 94: triggers, still below entry -> profit
        self.assertEqual(short_exit_decision(P, trigger, DOWN_BTC, 90, C), "trailing_profit")

    def test_default_sell_fn_uses_the_configured_btc_filter_mode(self):
        # Regression (18 Sep 2026): a short opened under btc_filter="loose"
        # must not be force-exited by a default exit check that silently
        # uses "strict" -- config["btc_filter"] must be honored.
        held = dict(F, c=100)  # short_exit level (150) not broken by price alone
        strict = short_exit_decision(P, held, CHOPPY_DOWN, 100, C)
        loose = short_exit_decision(P, held, CHOPPY_DOWN, 100, dict(C, btc_filter="loose"))
        self.assertEqual(strict, "emergency_risk")  # strict btc_down_ok fails -> forced exit
        self.assertIsNone(loose)  # loose btc_down_ok holds -> no forced exit

    def test_sell_fn_override_replaces_the_default_trend_check(self):
        held = dict(F, c=93)
        self.assertIsNone(short_exit_decision(P, held, DOWN_BTC, 93, C))
        self.assertEqual(short_exit_decision(P, held, DOWN_BTC, 93, C, sell_fn=lambda f, b: True),
                         "profit_signal")


class ExecuteShortExitTests(unittest.TestCase):
    """Close first, then cancel the stop (24 Sep 2026): the close no longer
    waits for a stop cancel Binance may be slow to confirm."""

    def _executor(self, amount="-0.1"):
        e = Mock()
        e.position_risk.return_value = [{"symbol": "SOLUSDT", "positionAmt": amount}]
        e.query.side_effect = OrderRejected(-2013)
        e.market_close_short.return_value = {"status": "FILLED"}
        return e

    def test_closes_what_is_open_then_cancels_the_stop(self):
        e = self._executor()
        r = execute_short_exit(P, "profit_signal", e)
        self.assertEqual(r["status"], "closed")
        e.market_close_short.assert_called_once_with("SOLUSDT", "0.1", "kv1fx7")
        e.cancel.assert_called_once_with("SOLUSDT", "kv1fp7")
        self.assertLess(e.method_calls.index(call.market_close_short("SOLUSDT", "0.1", "kv1fx7")),
                        e.method_calls.index(call.cancel("SOLUSDT", "kv1fp7")))

    def test_nothing_open_means_the_stop_already_did_it(self):
        e = self._executor(amount="0")
        self.assertEqual(execute_short_exit(P, "emergency_risk", e)["status"], "already_stopped")
        e.market_close_short.assert_not_called()
        e.cancel.assert_called_once_with("SOLUSDT", "kv1fp7")

    def test_a_failed_cancel_after_the_close_does_not_undo_the_exit(self):
        e = self._executor()
        e.cancel.side_effect = OrderStateUnknown("unknown")
        self.assertEqual(execute_short_exit(P, "profit_signal", e)["status"], "closed")


P_LONG = {"symbol": "ADAUSDT", "entry": 0.24, "stop_price": 0.23, "quantity": "50",
          "stop_client_id": "kv1fq7"}


class ExecuteLongFuturesExitTests(unittest.TestCase):
    """Mirrors ExecuteShortExitTests, side-flipped: market_close_long, never
    market_close_short."""

    def _executor(self, amount="50"):
        e = Mock()
        e.position_risk.return_value = [{"symbol": "ADAUSDT", "positionAmt": amount}]
        e.query.side_effect = OrderRejected(-2013)
        e.market_close_long.return_value = {"status": "FILLED"}
        return e

    def test_closes_what_is_open_then_cancels_the_stop(self):
        e = self._executor(amount="48")  # the exchange's own amount, not the stop's
        r = execute_long_futures_exit(P_LONG, "trailing_profit", e)
        self.assertEqual(r["status"], "closed")
        e.market_close_long.assert_called_once_with("ADAUSDT", "48", "kv1fy7")
        e.market_close_short.assert_not_called()
        e.cancel.assert_called_once_with("ADAUSDT", "kv1fq7")

    def test_a_moved_stop_s_exit_id_stays_within_36_characters(self):
        e = self._executor()
        position = dict(P_LONG, stop_client_id="kv1fq497791790193600000t1790223582")
        execute_long_futures_exit(position, "trailing_profit", e)
        exit_id = e.market_close_long.call_args.args[2]
        self.assertEqual(exit_id, "kv1fy497791790193600000t1790223582")
        self.assertLessEqual(len(exit_id), 36)

    def test_a_short_position_is_never_closed_as_a_long(self):
        e = self._executor(amount="-50")
        self.assertEqual(execute_long_futures_exit(P_LONG, "trailing_profit", e)["status"], "already_stopped")
        e.market_close_long.assert_not_called()

    def test_reduce_only_rejection_after_the_position_closed_cancels_the_stop(self):
        e = self._executor()
        e.position_risk.side_effect = [[{"symbol": "ADAUSDT", "positionAmt": "50"}],
                                       [{"symbol": "ADAUSDT", "positionAmt": "0"}]]
        e.market_close_long.side_effect = OrderRejected(-2022, "ReduceOnly Order is rejected.")
        self.assertEqual(execute_long_futures_exit(P_LONG, "trailing_profit", e)["status"], "already_stopped")
        e.cancel.assert_called_once_with("ADAUSDT", "kv1fq7")

    def test_reduce_only_rejection_with_the_position_still_open_keeps_the_stop(self):
        # Audit A6: -2022 was taken to mean "already closed" and the stop
        # went, even when the position was still there.
        e = self._executor()
        e.market_close_long.side_effect = OrderRejected(-2022, "ReduceOnly Order is rejected.")
        with self.assertRaises(RuntimeError):
            execute_long_futures_exit(P_LONG, "trailing_profit", e)
        e.cancel.assert_not_called()

    def test_any_other_rejection_is_raised(self):
        e = self._executor()
        e.market_close_long.side_effect = OrderRejected(-1003)
        with self.assertRaises(OrderRejected):
            execute_long_futures_exit(P_LONG, "trailing_profit", e)
        e.cancel.assert_not_called()


if __name__ == '__main__':
    unittest.main()
