import unittest
from unittest.mock import Mock
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
    def test_cancel_stop_then_close_once(self):
        e = Mock()
        e.cancel.return_value = {"status": "CANCELED"}
        e.query.side_effect = OrderRejected(-2013)
        e.market_close_short.return_value = {"status": "FILLED"}
        r = execute_short_exit(P, "profit_signal", e)
        self.assertEqual(r["status"], "closed")
        e.market_close_short.assert_called_once()

    def test_ambiguous_cancel_is_queried(self):
        e = Mock()
        e.cancel.side_effect = OrderStateUnknown("unknown")
        e.query.return_value = {"status": "FILLED"}
        self.assertEqual(execute_short_exit(P, "emergency_risk", e)["status"], "already_stopped")
        e.market_close_short.assert_not_called()


P_LONG = {"symbol": "ADAUSDT", "entry": 0.24, "stop_price": 0.23, "quantity": "50",
         "stop_client_id": "kv1fq7"}


class ExecuteLongFuturesExitTests(unittest.TestCase):
    """Mirrors ExecuteShortExitTests exactly, side-flipped: cancels the
    long's protective stop (kv1fq) then market-closes via
    market_close_long, never market_close_short."""

    def test_cancel_stop_then_close_once(self):
        e = Mock()
        e.cancel.return_value = {"status": "CANCELED"}
        e.query.side_effect = OrderRejected(-2013)
        e.market_close_long.return_value = {"status": "FILLED"}
        r = execute_long_futures_exit(P_LONG, "trailing_profit", e)
        self.assertEqual(r["status"], "closed")
        e.market_close_long.assert_called_once()
        e.market_close_short.assert_not_called()

    def test_ambiguous_cancel_is_queried(self):
        e = Mock()
        e.cancel.side_effect = OrderStateUnknown("unknown")
        e.query.return_value = {"status": "FILLED"}
        self.assertEqual(execute_long_futures_exit(P_LONG, "emergency_risk", e)["status"], "already_stopped")
        e.market_close_long.assert_not_called()

    def test_unconfirmed_cancel_state_raises(self):
        e = Mock()
        e.cancel.return_value = {"status": "PARTIALLY_FILLED"}
        with self.assertRaises(RuntimeError):
            execute_long_futures_exit(P_LONG, "trailing_profit", e)


if __name__ == '__main__':
    unittest.main()
