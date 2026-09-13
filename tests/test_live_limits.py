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

    def test_sat_confirms_only_one_recent_profit_exit(self):
        config = dict(C, telegram_sell_command="SAT", profit_exit_confirmation_expiry_minutes=10)
        signal = {"symbol": "BTCUSDT", "created_at": 1000, "reason": "profit_signal"}
        self.assertEqual(confirmed_profit_exit("sat", [signal], 2000, config)[1], "confirmed")
        self.assertEqual(confirmed_profit_exit("SAT", [], 2000, config)[1], "pending_exit_count")
        self.assertEqual(confirmed_profit_exit("SAT", [dict(signal, reason="stop_loss")], 2000, config)[1],
                         "not_profit_exit")
        self.assertEqual(confirmed_profit_exit("SAT", [signal], 700000, config)[1], "exit_signal_expired")
