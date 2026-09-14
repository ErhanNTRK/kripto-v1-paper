import unittest
from decimal import Decimal
from unittest.mock import Mock

from crypto_v1.binance_trade import OrderRejected, OrderStateUnknown
from crypto_v1.live_controller import approve_buy
from crypto_v1.live_execution import LIVE_PHRASE


C = {"live_trading_enabled": False, "telegram_buy_command": "AL",
     "signal_confirmation_expiry_minutes": 10, "max_open_positions": 1,
     "max_buys_per_day": 4, "daily_loss_limit_usdt": 1.36,
     "pilot_loss_limit_usdt": 6.81, "risk_per_trade_usdt": 0.34,
     "max_entry_drift_fraction": 0.005}
SAVED = {"state": {"pending_buys": {"SOLUSDT": {"stop": 95}},
                   "events": [{"type": "AL_ADAYI", "time": 500000,
                               "symbol": "SOLUSDT", "close": 100}]}}
RULES = {"tick_size": Decimal("0.01"), "step_size": Decimal("0.001"),
         "min_qty": Decimal("0.001"), "min_notional": Decimal("5")}


class Market:
    def pilot_status(self):
        return {"open_positions": 0, "buys_today": 0, "realized_loss_today": 0,
                "pilot_drawdown": 0, "free_usdt": 68.13}
    def price(self, symbol): return 100
    def rules(self, symbol): return RULES


class LiveControllerTests(unittest.TestCase):
    def test_disabled_mode_returns_plan_without_network(self):
        executor = Mock()
        result = approve_buy(7, "AL", 501000, SAVED, C, {}, Market(), executor)
        self.assertEqual(result["status"], "preview")
        executor.assert_not_called()

    def test_stale_or_wrong_command_is_rejected(self):
        self.assertEqual(approve_buy(7, "SAT", 501000, SAVED, C, {}, Market(), Mock())["status"],
                         "rejected")

    def test_enabled_mode_buys_once_and_places_stop(self):
        executor = Mock()
        executor.query.side_effect = [OrderRejected(-2013), OrderRejected(-2013)]
        executor.market_buy.return_value = {"status": "FILLED", "executedQty": "0.34"}
        executor.protective_stop.return_value = {"status": "NEW"}
        env = {"LIVE_TRADING_CONFIRMATION": LIVE_PHRASE}
        result = approve_buy(7, "AL", 501000, SAVED, dict(C, live_trading_enabled=True),
                             env, Market(), executor)
        self.assertEqual(result["status"], "bought_and_protected")
        executor.market_buy.assert_called_once()
        executor.protective_stop.assert_called_once()

    def test_ambiguous_buy_is_queried_not_repeated(self):
        executor = Mock()
        executor.query.side_effect = [OrderRejected(-2013),
                                      {"status": "FILLED", "executedQty": "0.34"},
                                      OrderRejected(-2013)]
        executor.market_buy.side_effect = OrderStateUnknown("unknown")
        executor.protective_stop.return_value = {"status": "NEW"}
        result = approve_buy(8, "AL", 501000, SAVED, dict(C, live_trading_enabled=True),
                             {"LIVE_TRADING_CONFIRMATION": LIVE_PHRASE}, Market(), executor)
        self.assertEqual(result["status"], "bought_and_protected")
        executor.market_buy.assert_called_once()

    def test_rejected_stop_immediately_sells_the_fill(self):
        executor = Mock()
        executor.query.side_effect = [OrderRejected(-2013), OrderRejected(-2013),
                                      OrderRejected(-2013)]
        executor.market_buy.return_value = {"status": "FILLED", "executedQty": "0.34"}
        executor.protective_stop.side_effect = OrderRejected(-1013)
        executor.market_sell.return_value = {"status": "FILLED", "executedQty": "0.339"}
        result = approve_buy(9, "AL", 501000, SAVED, dict(C, live_trading_enabled=True),
                             {"LIVE_TRADING_CONFIRMATION": LIVE_PHRASE}, Market(), executor)
        self.assertEqual(result["status"], "bought_then_emergency_sold")
        executor.market_sell.assert_called_once()
