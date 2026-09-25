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


class ProportionalRiskTests(unittest.TestCase):
    """23 Sep 2026: risk and loss limits scale with each system's equity
    (user's decision: 0.75% per trade, 10% daily, 40% of baseline total)."""

    def test_risk_per_trade_is_a_share_of_current_equity(self):
        from decimal import Decimal
        from crypto_v1.live_limits import trade_risk_usdt
        config = {"risk_per_trade_usdt": 5.0, "risk_per_trade_fraction": 0.0075}
        self.assertEqual(trade_risk_usdt(config, Decimal("80")), Decimal("0.6000"))
        self.assertEqual(trade_risk_usdt(config, Decimal("160")), Decimal("1.2000"))

    def test_breakouts_risk_more_than_momentum_entries(self):
        # 25 Sep 2026: quality_risk_multipliers [breakout, momentum].
        from decimal import Decimal
        from crypto_v1.live_limits import trade_risk_usdt
        config = {"risk_per_trade_usdt": 5.0, "risk_per_trade_fraction": 0.0075,
                  "quality_risk_multipliers": [1.5, 0.5]}
        self.assertEqual(trade_risk_usdt(config, Decimal("100"), breaks_up=2), Decimal("1.125"))
        self.assertEqual(trade_risk_usdt(config, Decimal("100"), breaks_up=1), Decimal("1.125"))
        self.assertEqual(trade_risk_usdt(config, Decimal("100"), breaks_up=0), Decimal("0.375"))
        # Unknown signal type: plain size, never a guess.
        self.assertEqual(trade_risk_usdt(config, Decimal("100")), Decimal("0.75"))
        # Without the setting nothing changes.
        del config["quality_risk_multipliers"]
        self.assertEqual(trade_risk_usdt(config, Decimal("100"), breaks_up=0), Decimal("0.75"))

    def test_live_configs_split_30_70_with_quality_sizing(self):
        # 25 Sep 2026 decision: 4H 30% / 2H 70% of the wallet; loss-limit
        # baselines are the same split of the 156 USDT the systems started from.
        import json
        from pathlib import Path
        four = json.loads(Path("short_live_config.json").read_text(encoding="utf-8"))
        two = json.loads(Path("short_live_config_2h.json").read_text(encoding="utf-8"))
        self.assertEqual((four["pilot_capital_fraction"], two["pilot_capital_fraction"]), (0.3, 0.7))
        self.assertAlmostEqual(four["pilot_capital_usdt"] + two["pilot_capital_usdt"], 156.0)
        self.assertAlmostEqual(four["pilot_capital_usdt"] / 156.0, 0.3)
        for config in (four, two):
            self.assertEqual(config["quality_risk_multipliers"], [1.5, 0.5])
            self.assertEqual(config["risk_per_trade_fraction"], 0.0075)
            # The breakout size stays under the hard per-trade ceiling.
            self.assertLessEqual(config["risk_per_trade_fraction"] * 1.5, config["max_risk_per_trade_fraction"])

    def test_fixed_risk_is_used_without_the_fraction_or_equity(self):
        from decimal import Decimal
        from crypto_v1.live_limits import trade_risk_usdt
        self.assertEqual(trade_risk_usdt({"risk_per_trade_usdt": 5.0}, Decimal("80")), Decimal("5.0"))
        self.assertEqual(trade_risk_usdt({"risk_per_trade_usdt": 5.0, "risk_per_trade_fraction": 0.0075}, None),
                         Decimal("5.0"))

    def test_proportional_loss_limits(self):
        from crypto_v1.live_limits import may_open
        config = {"live_trading_enabled": True, "max_open_positions": 6, "max_buys_per_day": 40,
                  "daily_loss_limit_usdt": 12, "pilot_loss_limit_usdt": 15, "pilot_capital_usdt": 34.55,
                  "daily_loss_limit_fraction": 0.10, "pilot_loss_limit_fraction": 0.40}
        # 10% of 80 = 8 USDT daily limit.
        self.assertEqual(may_open(config, 0, 0, 7.9, 0, equity=80), (True, "allowed"))
        self.assertEqual(may_open(config, 0, 0, 8.0, 0, equity=80), (False, "daily_loss_limit"))
        # 40% of the 34.55 baseline = 13.82 USDT drawdown limit.
        self.assertEqual(may_open(config, 0, 0, 0, 13.9, equity=80), (False, "pilot_loss_limit"))

