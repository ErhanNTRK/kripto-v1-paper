import unittest
from decimal import Decimal
from unittest.mock import Mock

from crypto_v1.binance_trade import OrderRejected
from crypto_v1.live_execution import LIVE_PHRASE
from crypto_v1.live_short_controller import approve_long_leveraged, approve_short


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

    def test_rejects_reopening_a_symbol_already_held(self):
        class AlreadyHeldMarket(Market):
            def pilot_status(self):
                status = super().pilot_status()
                return dict(status, open_positions=1, held_symbols={"SOLUSDT"})
        executor = Mock()
        result = approve_short(7, "SHORT", 501000, SAVED, dict(C, live_trading_enabled=True),
                               {"LIVE_TRADING_CONFIRMATION": LIVE_PHRASE}, AlreadyHeldMarket(), executor)
        self.assertEqual(result, {"status": "rejected", "reason": "already_holding_symbol"})
        executor.market_open_short.assert_not_called()

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


C_LONG = dict(C, telegram_buy_command="AL")
SAVED_LONG_FIXED = {"state": {"pending_buys": {"SOLUSDT": {"stop": 95, "leverage": 2}},
                              "events": [{"type": "AL_ADAYI", "time": 500000,
                                          "symbol": "SOLUSDT", "close": 100}]}}
SAVED_LONG_TIERED_STRONG = {"state": {"pending_buys": {"SOLUSDT": {"stop": 95, "breaks_up": 3}},
                                      "events": [{"type": "AL_ADAYI", "time": 500000,
                                                  "symbol": "SOLUSDT", "close": 100}]}}
SAVED_LONG_TIERED_NORMAL = {"state": {"pending_buys": {"SOLUSDT": {"stop": 95, "breaks_up": 1}},
                                      "events": [{"type": "AL_ADAYI", "time": 500000,
                                                  "symbol": "SOLUSDT", "close": 100}]}}


class ApproveLongLeveragedTests(unittest.TestCase):
    """Mirrors LiveShortControllerTests for approve_long_leveraged (21 Sep
    2026), the side-flipped counterpart that replaces unleveraged Spot
    for new long entries -- same mechanics (isolated margin, leverage set
    before opening, liquidation-safety check before resting a stop), plus
    its own leverage-resolution rule: an explicit "leverage" on the
    candidate (2H's fixed 2x) wins; otherwise it's computed from
    "breaks_up" (4H's 3x/5x tiering, via leverage_for_signal)."""

    def test_an_explicit_leverage_on_the_candidate_is_used_as_is(self):
        executor = Mock()
        result = approve_long_leveraged(7, "AL", 501000, SAVED_LONG_FIXED, C_LONG, {}, Market(), executor)
        self.assertEqual(result["status"], "preview")
        self.assertEqual(result["plan"]["leverage"], 2)
        executor.assert_not_called()

    def test_no_explicit_leverage_falls_back_to_breaks_up_tiering(self):
        executor = Mock()
        strong = approve_long_leveraged(7, "AL", 501000, SAVED_LONG_TIERED_STRONG, C_LONG, {}, Market(), executor)
        self.assertEqual(strong["plan"]["leverage"], 5)
        normal = approve_long_leveraged(7, "AL", 501000, SAVED_LONG_TIERED_NORMAL, C_LONG, {}, Market(), executor)
        self.assertEqual(normal["plan"]["leverage"], 3)

    def test_stale_or_wrong_command_is_rejected(self):
        self.assertEqual(approve_long_leveraged(7, "SHORT", 501000, SAVED_LONG_FIXED, C_LONG, {}, Market(),
                                                 Mock())["status"], "rejected")

    def test_rejects_reopening_a_symbol_already_held(self):
        class AlreadyHeldMarket(Market):
            def pilot_status(self):
                status = super().pilot_status()
                return dict(status, open_positions=1, held_symbols={"SOLUSDT"})
        executor = Mock()
        result = approve_long_leveraged(7, "AL", 501000, SAVED_LONG_FIXED, dict(C_LONG, live_trading_enabled=True),
                                        {"LIVE_TRADING_CONFIRMATION": LIVE_PHRASE}, AlreadyHeldMarket(), executor)
        self.assertEqual(result, {"status": "rejected", "reason": "already_holding_symbol"})
        executor.market_open_long.assert_not_called()

    def test_enabled_mode_opens_once_sets_leverage_and_places_stop(self):
        executor = Mock()
        executor.query.side_effect = [OrderRejected(-2013), OrderRejected(-2013)]
        executor.market_open_long.return_value = {"status": "FILLED", "executedQty": "0.068"}
        executor.position_risk.return_value = [{"symbol": "SOLUSDT", "positionAmt": "0.068",
                                                 "liquidationPrice": "50"}]
        executor.protective_stop_for_long.return_value = {"status": "NEW"}
        env = {"LIVE_TRADING_CONFIRMATION": LIVE_PHRASE}
        result = approve_long_leveraged(7, "AL", 501000, SAVED_LONG_FIXED, dict(C_LONG, live_trading_enabled=True),
                                        env, Market(), executor)
        self.assertEqual(result["status"], "opened_and_protected")
        executor.set_isolated_margin.assert_called_once_with("SOLUSDT")
        executor.set_leverage.assert_called_once_with("SOLUSDT", 2)
        executor.market_open_long.assert_called_once()
        executor.protective_stop_for_long.assert_called_once()

    def test_liquidation_too_close_triggers_emergency_close_not_a_resting_stop(self):
        executor = Mock()
        executor.query.side_effect = [OrderRejected(-2013), OrderRejected(-2013)]
        executor.market_open_long.return_value = {"status": "FILLED", "executedQty": "0.068"}
        # liquidation at 94 is inside the required 20% buffer below the 95 stop.
        executor.position_risk.return_value = [{"symbol": "SOLUSDT", "positionAmt": "0.068",
                                                 "liquidationPrice": "94"}]
        executor.market_close_long.return_value = {"status": "FILLED", "executedQty": "0.068"}
        env = {"LIVE_TRADING_CONFIRMATION": LIVE_PHRASE}
        result = approve_long_leveraged(7, "AL", 501000, SAVED_LONG_FIXED, dict(C_LONG, live_trading_enabled=True),
                                        env, Market(), executor)
        self.assertEqual(result["status"], "opened_then_emergency_closed")
        self.assertEqual(result["reason"], "liquidation_too_close")
        executor.market_close_long.assert_called_once()
        executor.protective_stop_for_long.assert_not_called()

    def test_rejected_stop_immediately_closes_the_fill(self):
        executor = Mock()
        executor.query.side_effect = [OrderRejected(-2013), OrderRejected(-2013),
                                      OrderRejected(-2013)]
        executor.market_open_long.return_value = {"status": "FILLED", "executedQty": "0.068"}
        executor.position_risk.return_value = [{"symbol": "SOLUSDT", "positionAmt": "0.068",
                                                 "liquidationPrice": "50"}]
        executor.protective_stop_for_long.side_effect = OrderRejected(-1013)
        executor.market_close_long.return_value = {"status": "FILLED", "executedQty": "0.068"}
        result = approve_long_leveraged(9, "AL", 501000, SAVED_LONG_FIXED, dict(C_LONG, live_trading_enabled=True),
                                        {"LIVE_TRADING_CONFIRMATION": LIVE_PHRASE}, Market(), executor)
        self.assertEqual(result["status"], "opened_then_emergency_closed")
        self.assertEqual(result["reason"], "protective_stop_rejected")
        executor.market_close_long.assert_called_once()


if __name__ == '__main__':
    unittest.main()
