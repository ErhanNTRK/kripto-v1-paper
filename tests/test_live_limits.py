import unittest
from crypto_v1.live_limits import may_open, confirmed_signal, exit_action, confirmed_profit_exit


C = {"live_trading_enabled": True, "max_open_positions": 1,
     "max_buys_per_day": 4, "daily_loss_limit_usdt": 1.36,
     "pilot_loss_limit_usdt": 6.81, "telegram_buy_command": "AL",
     "signal_confirmation_expiry_minutes": 10}


class LiveLimitTests(unittest.TestCase):
    def test_live_is_fail_closed(self):
        self.assertEqual(may_open(dict(C, live_trading_enabled=False), 0, 0, 0, 0)[1], "live_disabled")

    def test_all_limits(self):
        self.assertTrue(may_open(C, 0, 3, 1.35, 6.80)[0])
        self.assertEqual(may_open(C, 1, 0, 0, 0)[1], "position_limit")
        self.assertEqual(may_open(C, 0, 4, 0, 0)[1], "daily_buy_limit")
        self.assertEqual(may_open(C, 0, 0, 1.36, 0)[1], "daily_loss_limit")
        self.assertEqual(may_open(C, 0, 0, 0, 6.81)[1], "pilot_loss_limit")

    def test_rejects_a_symbol_already_held_even_with_room_under_other_limits(self):
        # Regression (18 Sep 2026): with entries now automatic, a strong
        # sustained breakout can keep re-firing a candidate for a symbol
        # already held across several consecutive bars -- open_positions
        # alone only caps the TOTAL count, never per-symbol.
        allowed, reason = may_open(C, 0, 0, 0, 0, symbol="SOLUSDT", held_symbols={"SOLUSDT"})
        self.assertFalse(allowed)
        self.assertEqual(reason, "already_holding_symbol")

    def test_allows_a_symbol_not_already_held(self):
        allowed, reason = may_open(C, 0, 0, 0, 0, symbol="ETHUSDT", held_symbols={"SOLUSDT"})
        self.assertTrue(allowed)

    def test_symbol_check_is_opt_in_for_backward_compatibility(self):
        # No symbol/held_symbols given -> behaves exactly as before.
        self.assertTrue(may_open(C, 0, 0, 0, 0)[0])

    def test_al_only_confirms_one_recent_signal(self):
        signal = {"symbol": "BTCUSDT", "created_at": 1000}
        self.assertEqual(confirmed_signal("al", [signal], 2000, C)[1], "confirmed")
        self.assertEqual(confirmed_signal("AL", [], 2000, C)[1], "pending_signal_count")
        self.assertEqual(confirmed_signal("AL", [signal], 700000, C)[1], "signal_expired")
        self.assertEqual(confirmed_signal("SAT", [signal], 2000, C)[1], "invalid_command")

    def test_risk_exit_never_waits_for_telegram(self):
        config = dict(C, automatic_risk_exits=True, profit_exit_confirmation_required=True)
        self.assertEqual(exit_action("stop_loss", config), ("sell_now", "risk_exit"))
        self.assertEqual(exit_action("emergency_risk", config), ("sell_now", "risk_exit"))

    def test_profit_exit_waits_for_sat(self):
        config = dict(C, automatic_risk_exits=True, profit_exit_confirmation_required=True)
        self.assertEqual(exit_action("take_profit", config),
                         ("notify_and_wait", "profit_confirmation_required"))

    def test_profit_exit_is_automatic_when_configured(self):
        config = dict(C, automatic_risk_exits=True, profit_exit_confirmation_required=False)
        self.assertEqual(exit_action("take_profit", config), ("sell_now", "profit_exit"))
        self.assertEqual(exit_action("trailing_profit", config), ("sell_now", "profit_exit"))
        self.assertEqual(exit_action("profit_signal", config), ("sell_now", "profit_exit"))

    def test_sat_confirms_only_one_recent_profit_exit(self):
        config = dict(C, telegram_sell_command="SAT", profit_exit_confirmation_expiry_minutes=10)
        signal = {"symbol": "BTCUSDT", "created_at": 1000, "reason": "profit_signal"}
        self.assertEqual(confirmed_profit_exit("sat", [signal], 2000, config)[1], "confirmed")
        self.assertEqual(confirmed_profit_exit("SAT", [], 2000, config)[1], "pending_exit_count")
        self.assertEqual(confirmed_profit_exit("SAT", [dict(signal, reason="stop_loss")], 2000, config)[1],
                         "not_profit_exit")
        self.assertEqual(confirmed_profit_exit("SAT", [signal], 700000, config)[1], "exit_signal_expired")
