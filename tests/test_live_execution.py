import unittest
from decimal import Decimal

from crypto_v1.live_execution import (LIVE_PHRASE, execution_enabled,
                                       protective_order_plan, symbol_rules)


class LiveExecutionTests(unittest.TestCase):
    def test_two_independent_switches_are_required(self):
        self.assertFalse(execution_enabled({"live_trading_enabled": False},
                                           {"LIVE_TRADING_CONFIRMATION": LIVE_PHRASE}))
        self.assertFalse(execution_enabled({"live_trading_enabled": True}, {}))
        self.assertTrue(execution_enabled({"live_trading_enabled": True},
                                          {"LIVE_TRADING_CONFIRMATION": LIVE_PHRASE}))

    def test_rules_and_risk_plan_round_down(self):
        rules = symbol_rules({"filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
            {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
            {"filterType": "MIN_NOTIONAL", "minNotional": "5"},
        ]})
        plan = protective_order_plan("20", "19", "68.13", "0.34", rules)
        self.assertEqual(plan["quantity"], "0.34")
        self.assertEqual(plan["stop_price"], "19")
        self.assertEqual(plan["stop_limit_price"], "18.99")
        self.assertLessEqual(Decimal(plan["planned_loss_usdt"]), Decimal("0.34"))

    def test_minimum_order_fails_closed(self):
        rules = {"tick_size": Decimal("0.01"), "step_size": Decimal("0.001"),
                 "min_qty": Decimal("0.001"), "min_notional": Decimal("10")}
        with self.assertRaises(ValueError):
            protective_order_plan("100", "99", "5", "0.34", rules)

    def test_invalid_stop_fails_closed(self):
        rules = {"tick_size": Decimal("0.01"), "step_size": Decimal("0.001"),
                 "min_qty": Decimal("0.001"), "min_notional": Decimal("5")}
        with self.assertRaises(ValueError):
            protective_order_plan("10", "10", "68.13", "0.34", rules)
