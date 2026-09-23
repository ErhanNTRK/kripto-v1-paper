import unittest
from decimal import Decimal

from crypto_v1.live_execution import (LIVE_PHRASE, execution_enabled,
                                       estimated_liquidation_price, futures_symbol_rules,
                                       leveraged_order_plan, liquidation_is_safe,
                                       protective_order_plan, safe_leverage,
                                       quote_amount, symbol_rules)


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

    def test_rules_carry_quote_precision_from_exchange_info_defaulting_to_8(self):
        filters = [
            {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
            {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
            {"filterType": "MIN_NOTIONAL", "minNotional": "5"},
        ]
        self.assertEqual(symbol_rules({"filters": filters, "quoteAssetPrecision": 6})["quote_precision"], 6)
        self.assertEqual(symbol_rules({"filters": filters})["quote_precision"], 8)

    def test_quote_amount_never_exceeds_the_quote_precision(self):
        # Regression (21 Sep 2026): stepSize- and lastPrice-formatted
        # Decimals both carry 8 decimals, so their product carried 16 and
        # every real market buy was rejected with -1111 BAD_PRECISION.
        quote = quote_amount("0.03800000", "261.40000000", {"quote_precision": 8})
        self.assertEqual(quote, "9.93320000")
        self.assertGreaterEqual(Decimal(quote).as_tuple().exponent, -8)
        # Rounds DOWN, never up, and honors a smaller precision.
        self.assertEqual(quote_amount("0.0123", "3.33333", {"quote_precision": 2}), "0.04")
        self.assertEqual(Decimal(quote_amount("1", "5", {})), Decimal("5"))

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


class FuturesSymbolRulesTests(unittest.TestCase):
    def test_prefers_market_lot_size_over_lot_size(self):
        rules = futures_symbol_rules({"filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
            {"filterType": "LOT_SIZE", "stepSize": "0.01", "minQty": "0.01"},
            {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
            {"filterType": "MIN_NOTIONAL", "notional": "5"},
        ]})
        self.assertEqual(rules["step_size"], Decimal("0.001"))
        self.assertEqual(rules["min_qty"], Decimal("0.001"))

    def test_falls_back_to_lot_size_when_no_market_lot_size(self):
        rules = futures_symbol_rules({"filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
            {"filterType": "LOT_SIZE", "stepSize": "0.01", "minQty": "0.01"},
            {"filterType": "MIN_NOTIONAL", "notional": "5"},
        ]})
        self.assertEqual(rules["step_size"], Decimal("0.01"))

    def test_min_notional_uses_the_futures_field_name(self):
        rules = futures_symbol_rules({"filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
            {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
            {"filterType": "MIN_NOTIONAL", "notional": "5"},
        ]})
        self.assertEqual(rules["min_notional"], Decimal("5"))

    def test_missing_notional_filter_fails_closed(self):
        with self.assertRaises(ValueError):
            futures_symbol_rules({"filters": [
                {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
            ]})


class LeveragedOrderPlanTests(unittest.TestCase):
    RULES = {"tick_size": Decimal("0.01"), "step_size": Decimal("0.001"),
             "min_qty": Decimal("0.001"), "min_notional": Decimal("1")}

    def test_long_plan_sizes_by_fixed_risk_like_spot(self):
        plan = leveraged_order_plan("long", "20", "19", "68.13", "0.34", 3, self.RULES)
        self.assertEqual(plan["quantity"], "0.34")
        self.assertEqual(plan["stop_price"], "19")
        self.assertEqual(plan["leverage"], 3)
        self.assertEqual(plan["side"], "long")
        self.assertLessEqual(Decimal(plan["planned_loss_usdt"]), Decimal("0.34") * Decimal("1.05"))
        self.assertLessEqual(Decimal(plan["margin_usdt"]), Decimal("68.13"))

    def test_short_plan_mirrors_long_with_stop_above_entry(self):
        plan = leveraged_order_plan("short", "19", "20", "68.13", "0.34", 3, self.RULES)
        self.assertEqual(plan["quantity"], "0.34")
        self.assertEqual(plan["stop_price"], "20")
        self.assertEqual(plan["side"], "short")

    def test_long_requires_stop_below_entry(self):
        with self.assertRaises(ValueError):
            leveraged_order_plan("long", "19", "20", "68.13", "0.34", 3, self.RULES)

    def test_short_requires_stop_above_entry(self):
        with self.assertRaises(ValueError):
            leveraged_order_plan("short", "20", "19", "68.13", "0.34", 3, self.RULES)

    def test_margin_cap_shrinks_quantity_when_free_capital_is_scarce(self):
        # risk_usdt alone would ask for qty=1.0 (100/1), but only 1 USDT of
        # free capital at 3x leverage can support far less notional.
        plan = leveraged_order_plan("long", "100", "99", "1", "100", 3, self.RULES)
        self.assertLess(Decimal(plan["quantity"]), Decimal("1.0"))
        self.assertLessEqual(Decimal(plan["margin_usdt"]), Decimal("1"))

    def test_scarce_margin_at_low_leverage_fails_closed_below_minimums(self):
        # The margin cap (free_usdt*0.9*leverage/entry) always keeps
        # margin_usdt <= free_usdt*0.9 by construction, so with 1 cent of
        # free capital at 1x leverage the capped quantity rounds down to
        # zero and is rejected as below Binance's minimums -- never
        # silently opens a position bigger than the allocated capital.
        with self.assertRaises(ValueError):
            leveraged_order_plan("long", "20000", "19999", "0.01", "1000", 1, self.RULES)


class SafeLeverageTests(unittest.TestCase):
    """The 3x/5x tier is a ceiling; the stop distance decides. Numbers use
    the module's assumed 2.5% maintenance margin and the 20% buffer."""

    def test_estimated_liquidation_price_long_and_short(self):
        self.assertAlmostEqual(float(estimated_liquidation_price("long", "100", 4)), 76.923, places=3)
        self.assertAlmostEqual(float(estimated_liquidation_price("short", "100", 4)), 121.951, places=3)

    def test_a_typical_7pct_long_stop_lands_on_4x_not_the_5x_tier(self):
        self.assertEqual(safe_leverage("long", "100", "93", 5), 4)

    def test_a_wider_long_stop_steps_down_to_3x(self):
        self.assertEqual(safe_leverage("long", "100", "91.5", 5), 3)

    def test_the_tier_stays_a_ceiling_when_the_stop_would_allow_more(self):
        self.assertEqual(safe_leverage("long", "100", "95", 3), 3)

    def test_a_very_tight_long_stop_reaches_the_full_5x_tier(self):
        self.assertEqual(safe_leverage("long", "100", "99", 5), 5)

    def test_short_side_is_tighter_under_the_price_based_buffer(self):
        # The buffer is 20% of the LIQUIDATION price; a short's liquidation
        # sits above entry, so the same 20% eats far more of the room.
        self.assertEqual(safe_leverage("short", "100", "105", 5), 2)
        self.assertEqual(safe_leverage("short", "100", "103", 5), 3)

    def test_a_stop_too_wide_for_even_2x_returns_none(self):
        self.assertIsNone(safe_leverage("long", "100", "50", 5))
        self.assertIsNone(safe_leverage("short", "100", "125", 5))

    def test_the_chosen_leverage_passes_the_real_check_and_the_next_one_up_fails(self):
        for side, entry, stop in (("long", "100", "93"), ("long", "100", "91.5"),
                                  ("short", "100", "105"), ("short", "100", "103")):
            chosen = safe_leverage(side, entry, stop, 5)
            self.assertTrue(liquidation_is_safe(side, stop, estimated_liquidation_price(side, entry, chosen)))
            self.assertFalse(liquidation_is_safe(side, stop, estimated_liquidation_price(side, entry, chosen + 1)))


class LiquidationIsSafeTests(unittest.TestCase):
    def test_long_is_safe_when_stop_is_well_above_liquidation(self):
        self.assertTrue(liquidation_is_safe("long", stop_price="19", liquidation_price="15"))

    def test_long_is_unsafe_when_stop_is_close_to_liquidation(self):
        self.assertFalse(liquidation_is_safe("long", stop_price="19", liquidation_price="18"))

    def test_short_is_safe_when_stop_is_well_below_liquidation(self):
        self.assertTrue(liquidation_is_safe("short", stop_price="21", liquidation_price="30"))

    def test_short_is_unsafe_when_stop_is_close_to_liquidation(self):
        self.assertFalse(liquidation_is_safe("short", stop_price="21", liquidation_price="25"))

    def test_zero_liquidation_price_is_never_safe(self):
        self.assertFalse(liquidation_is_safe("long", stop_price="19", liquidation_price="0"))


class MinimumNotionalFloorTests(unittest.TestCase):
    """max_risk_usdt (23 Sep 2026): a trade sized under Binance's minimum is
    rounded up to it only while the loss at the stop stays within the hard
    ceiling; otherwise it is still skipped."""
    RULES = {"step_size": Decimal("0.1"), "tick_size": Decimal("0.0001"),
             "min_qty": Decimal("0.1"), "min_notional": Decimal("5")}

    def test_below_minimum_is_rounded_up_within_the_ceiling(self):
        # 0.27 risk at a 6% stop = 4.5 USDT notional; rounded up to 5 USDT (~0.30 loss).
        plan = leveraged_order_plan("long", Decimal("1.0"), Decimal("0.94"), Decimal("36"),
                                    Decimal("0.27"), 4, self.RULES, max_risk_usdt=Decimal("0.54"))
        self.assertEqual(Decimal(plan["quantity"]), Decimal("5.0"))
        self.assertLessEqual(Decimal(plan["planned_loss_usdt"]), Decimal("0.54"))

    def test_skipped_when_the_minimum_would_exceed_the_ceiling(self):
        # 15% stop: 5 USDT minimum loses 0.75 > 0.54 ceiling.
        with self.assertRaises(ValueError):
            leveraged_order_plan("long", Decimal("1.0"), Decimal("0.85"), Decimal("36"),
                                 Decimal("0.27"), 4, self.RULES, max_risk_usdt=Decimal("0.54"))

    def test_without_a_ceiling_the_old_skip_applies(self):
        with self.assertRaises(ValueError):
            leveraged_order_plan("long", Decimal("1.0"), Decimal("0.94"), Decimal("36"),
                                 Decimal("0.27"), 4, self.RULES)

