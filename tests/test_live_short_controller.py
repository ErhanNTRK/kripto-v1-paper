import unittest
from unittest.mock import MagicMock
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
        # The signal's 3x is a ceiling (22 Sep 2026): with a 5% stop the
        # price-based 20% liquidation buffer only admits 2x on the short
        # side (see live_execution.safe_leverage).
        self.assertEqual(result["plan"]["leverage"], 2)
        executor.assert_not_called()

    def test_a_tight_stop_keeps_the_signals_full_tier(self):
        tight = {"state": {"pending_shorts": {"SOLUSDT": {"stop": 102, "leverage": 3}},
                           "events": SAVED["state"]["events"]}}
        result = approve_short(7, "SHORT", 501000, tight, C, {}, Market(), Mock())
        self.assertEqual(result["plan"]["leverage"], 3)

    def test_a_stop_too_wide_for_any_safe_leverage_is_rejected_before_any_order(self):
        wide = {"state": {"pending_shorts": {"SOLUSDT": {"stop": 130, "leverage": 3}},
                          "events": SAVED["state"]["events"]}}
        executor = Mock()
        result = approve_short(7, "SHORT", 501000, wide, dict(C, live_trading_enabled=True),
                               {"LIVE_TRADING_CONFIRMATION": LIVE_PHRASE}, Market(), executor)
        self.assertEqual(result, {"status": "rejected", "reason": "stop_too_wide_for_safe_leverage"})
        executor.market_open_short.assert_not_called()

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
        executor.set_leverage.assert_called_once_with("SOLUSDT", 2)  # 5% stop -> 2x, see above
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

    def test_no_explicit_leverage_falls_back_to_breaks_up_tiering_as_a_ceiling(self):
        executor = Mock()
        # Strong signal -> 5x tier, but a 5% stop only admits 4x under the
        # liquidation buffer (22 Sep 2026: the 5x tier previously opened and
        # was emergency-closed every time with real 5-9% ATR stops).
        strong = approve_long_leveraged(7, "AL", 501000, SAVED_LONG_TIERED_STRONG, C_LONG, {}, Market(), executor)
        self.assertEqual(strong["plan"]["leverage"], 4)
        normal = approve_long_leveraged(7, "AL", 501000, SAVED_LONG_TIERED_NORMAL, C_LONG, {}, Market(), executor)
        self.assertEqual(normal["plan"]["leverage"], 3)

    def test_a_tight_stop_reaches_the_full_5x_tier(self):
        tight = {"state": {"pending_buys": {"SOLUSDT": {"stop": 99, "breaks_up": 3}},
                           "events": SAVED_LONG_TIERED_STRONG["state"]["events"]}}
        result = approve_long_leveraged(7, "AL", 501000, tight, C_LONG, {}, Market(), Mock())
        self.assertEqual(result["plan"]["leverage"], 5)

    def test_a_stop_too_wide_for_any_safe_leverage_is_rejected_before_any_order(self):
        wide = {"state": {"pending_buys": {"SOLUSDT": {"stop": 50, "breaks_up": 3}},
                          "events": SAVED_LONG_TIERED_STRONG["state"]["events"]}}
        executor = Mock()
        result = approve_long_leveraged(7, "AL", 501000, wide, dict(C_LONG, live_trading_enabled=True),
                                        {"LIVE_TRADING_CONFIRMATION": LIVE_PHRASE}, Market(), executor)
        self.assertEqual(result, {"status": "rejected", "reason": "stop_too_wide_for_safe_leverage"})
        executor.market_open_long.assert_not_called()

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


class SettleFillTests(unittest.TestCase):
    """A Futures MARKET response can read NEW/0 while the order fills a
    moment later (PROVEUSDT, 23 Sep 2026): the real state is queried."""

    def test_a_new_response_is_settled_by_querying(self):
        from crypto_v1.live_short_controller import _settle_fill
        executor = MagicMock()
        executor.query.return_value = {"status": "FILLED", "executedQty": "89.5"}
        order = _settle_fill(executor, "PROVEUSDT", "kv1fl2x", {"status": "NEW", "executedQty": "0"},
                             sleep=lambda s: None)
        self.assertEqual(order["status"], "FILLED")
        executor.query.assert_called_once_with("PROVEUSDT", "kv1fl2x")

    def test_an_already_filled_response_needs_no_query(self):
        from crypto_v1.live_short_controller import _settle_fill
        executor = MagicMock()
        order = _settle_fill(executor, "X", "id", {"status": "FILLED", "executedQty": "1"}, sleep=lambda s: None)
        self.assertEqual(order["status"], "FILLED")
        executor.query.assert_not_called()

    def test_gives_up_after_its_attempts(self):
        from crypto_v1.live_short_controller import _settle_fill
        executor = MagicMock()
        executor.query.return_value = {"status": "NEW", "executedQty": "0"}
        order = _settle_fill(executor, "X", "id", {"status": "NEW"}, attempts=3, sleep=lambda s: None)
        self.assertEqual(order["status"], "NEW")
        self.assertEqual(executor.query.call_count, 3)

