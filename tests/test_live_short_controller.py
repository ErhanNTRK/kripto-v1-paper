import unittest
from decimal import Decimal
from unittest.mock import Mock

from crypto_v1.binance_trade import OrderRejected
from crypto_v1.live_execution import LIVE_PHRASE
from crypto_v1.live_short_controller import approve_short


C = {"live_trading_enabled": False, "telegram_buy_command": "SHORT",
     "signal_confirmation_expiry_minutes": 10, "max_open_positions": 1,
     "max_buys_per_day": 4, "daily_loss_limit_usdt": 1.36,
     "pilot_loss_limit_usdt": 6.81, "risk_per_trade_usdt": 0.34,
     "max_entry_drift_fraction": 0.005}
SAVED = {"state": {"pending_shorts": {"SOLUSDT": {"stop": 105, "leverage": 3}},
                   "events": [{"type": "SHORT_ADAYI", "time": 500000,
                               "symbol": "SOLUSDT", "close": 100}]}}
RULES = {"tick_size": Decimal("0.01"), "step_size": Decimal("0.001"),
         "min_qty": Decimal("0.001"), "min_notional": Decimal("5")}


class Market:
    def pilot_status(self):
        return {"open_positions": 0, "opens_today": 0, "realized_loss_today": 0,
                "pilot_drawdown": 0, "free_usdt": 68.13}
    def price(self, symbol): return 100
    def rules(self, symbol): return RULES


class LiveShortControllerTests(unittest.TestCase):
    def test_disabled_mode_returns_plan_without_network(self):
        executor = Mock()
        result = approve_short(7, "SHORT", 501000, SAVED, C, {}, Market(), executor)
        self.assertEqual(result["status"], "preview")
        self.assertEqual(result["plan"]["leverage"], 3)
        executor.assert_not_called()

    def test_stale_or_wrong_command_is_rejected(self):
        self.assertEqual(approve_short(7, "AL", 501000, SAVED, C, {}, Market(), Mock())["status"],
                         "rejected")

    def test_enabled_mode_opens_once_sets_leverage_and_places_stop(self):
        executor = Mock()
        executor.query.side_effect = [OrderRejected(-2013), OrderRejected(-2013)]
        executor.market_open_short.return_value = {"status": "FILLED", "executedQty": "0.068"}
        executor.position_risk.return_value = [{"symbol": "SOLUSDT", "positionAmt": "-0.068",
                                                 "liquidationPrice": "150"}]
        executor.protective_stop_for_short.return_value = {"status": "NEW"}
        env = {"LIVE_TRADING_CONFIRMATION": LIVE_PHRASE}
        result = approve_short(7, "SHORT", 501000, SAVED, dict(C, live_trading_enabled=True),
                               env, Market(), executor)
        self.assertEqual(result["status"], "opened_and_protected")
        executor.set_isolated_margin.assert_called_once_with("SOLUSDT")
        executor.set_leverage.assert_called_once_with("SOLUSDT", 3)
        executor.market_open_short.assert_called_once()
        executor.protective_stop_for_short.assert_called_once()

    def test_liquidation_too_close_triggers_emergency_close_not_a_resting_stop(self):
        executor = Mock()
        executor.query.side_effect = [OrderRejected(-2013), OrderRejected(-2013)]
        executor.market_open_short.return_value = {"status": "FILLED", "executedQty": "0.068"}
        # liquidation at 106 is inside the required 20% buffer beyond the 105 stop.
        executor.position_risk.return_value = [{"symbol": "SOLUSDT", "positionAmt": "-0.068",
                                                 "liquidationPrice": "106"}]
        executor.market_close_short.return_value = {"status": "FILLED", "executedQty": "0.068"}
        env = {"LIVE_TRADING_CONFIRMATION": LIVE_PHRASE}
        result = approve_short(7, "SHORT", 501000, SAVED, dict(C, live_trading_enabled=True),
                               env, Market(), executor)
        self.assertEqual(result["status"], "opened_then_emergency_closed")
        self.assertEqual(result["reason"], "liquidation_too_close")
        executor.market_close_short.assert_called_once()
        executor.protective_stop_for_short.assert_not_called()

    def test_rejected_stop_immediately_closes_the_fill(self):
        executor = Mock()
        executor.query.side_effect = [OrderRejected(-2013), OrderRejected(-2013),
                                      OrderRejected(-2013)]
        executor.market_open_short.return_value = {"status": "FILLED", "executedQty": "0.068"}
        executor.position_risk.return_value = [{"symbol": "SOLUSDT", "positionAmt": "-0.068",
                                                 "liquidationPrice": "150"}]
        executor.protective_stop_for_short.side_effect = OrderRejected(-1013)
        executor.market_close_short.return_value = {"status": "FILLED", "executedQty": "0.068"}
        result = approve_short(9, "SHORT", 501000, SAVED, dict(C, live_trading_enabled=True),
                               {"LIVE_TRADING_CONFIRMATION": LIVE_PHRASE}, Market(), executor)
        self.assertEqual(result["status"], "opened_then_emergency_closed")
        self.assertEqual(result["reason"], "protective_stop_rejected")
        executor.market_close_short.assert_called_once()


if __name__ == '__main__':
    unittest.main()
