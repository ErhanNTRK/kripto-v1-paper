import unittest
from decimal import Decimal
from crypto_v1.live_market import summarize_pilot


class LiveMarketTests(unittest.TestCase):
    def test_status_is_reconstructed_from_account_and_orders(self):
        account = {"balances": [{"asset": "USDT", "free": "60", "locked": "0"},
                                {"asset": "SOL", "free": "0", "locked": "0.05"}]}
        open_orders = [{"symbol": "SOLUSDT", "clientOrderId": "kv1s7"}]
        orders = [{"side": "BUY", "status": "FILLED", "clientOrderId": "kv1b7",
                   "cummulativeQuoteQty": "6.8"},
                  {"side": "SELL", "status": "FILLED", "clientOrderId": "other",
                   "cummulativeQuoteQty": "999"}]
        result = summarize_pilot(account, open_orders, orders, {"SOLUSDT": Decimal("100")},
                                 {"pilot_capital_usdt": 68.13}, 0)
        self.assertEqual(result["open_positions"], 1)
        self.assertEqual(result["held_symbols"], {"SOLUSDT"})
        self.assertEqual(result["buys_today"], 1)
        self.assertEqual(result["realized_loss_today"], Decimal("0"))
        self.assertEqual(result["equity"], Decimal("65"))
        self.assertEqual(result["pilot_drawdown"], Decimal("3.13"))

    def test_completed_round_trip_counts_realized_loss(self):
        orders = [{"side": "BUY", "status": "FILLED", "clientOrderId": "kv1b1",
                   "cummulativeQuoteQty": "10", "time": 1},
                  {"side": "SELL", "status": "FILLED", "clientOrderId": "kv1x1",
                   "cummulativeQuoteQty": "9.7", "time": 20}]
        result = summarize_pilot({"balances": [{"asset": "USDT", "free": "67.83",
                                                 "locked": "0"}]}, [], orders, {},
                                 {"pilot_capital_usdt": 68.13}, 10)
        self.assertEqual(result["realized_loss_today"], Decimal("0.3197"))
        self.assertEqual(result["buys_today"], 0)


class TaggedSubSystemTests(unittest.TestCase):
    """Two systems (e.g. "4" for 4H, "2" for 2H) share the SAME real Spot
    wallet -- each must only see and spend its OWN slice of capital,
    tracked via its own all-time realized P&L, never the whole account's
    real balance (which the other tag also affects)."""

    CONFIG = {"pilot_capital_usdt": 17}

    def test_tag_equity_comes_from_its_own_realized_pnl_not_the_account_balance(self):
        # Real account balance says 1000 (irrelevant -- some of that could
        # be the OTHER tag's money); tag "4" made a net +3 profit on its
        # own one closed round trip (bought 10, sold back 13.03).
        orders = [{"side": "BUY", "status": "FILLED", "clientOrderId": "kv1b41",
                   "cummulativeQuoteQty": "10", "time": 1},
                  {"side": "SELL", "status": "FILLED", "clientOrderId": "kv1x41",
                   "cummulativeQuoteQty": "13.03", "time": 2}]
        account = {"balances": [{"asset": "USDT", "free": "1000", "locked": "0"}]}
        result = summarize_pilot(account, [], orders, {}, self.CONFIG, 0, tag="4")
        self.assertEqual(result["equity"], Decimal("17") + Decimal("13.03") * Decimal("0.999")
                         - Decimal("10") * Decimal("1.001"))
        self.assertEqual(result["pilot_drawdown"], Decimal("0"))  # net profit, floored at 0

    def test_orders_from_a_different_tag_never_leak_in(self):
        orders = [{"side": "BUY", "status": "FILLED", "clientOrderId": "kv1b41",
                   "cummulativeQuoteQty": "10", "time": 1},
                  {"side": "SELL", "status": "FILLED", "clientOrderId": "kv1x41",
                   "cummulativeQuoteQty": "5", "time": 2},   # tag 4: a real loss
                  {"side": "BUY", "status": "FILLED", "clientOrderId": "kv1b21",
                   "cummulativeQuoteQty": "10", "time": 1},
                  {"side": "SELL", "status": "FILLED", "clientOrderId": "kv1x21",
                   "cummulativeQuoteQty": "50", "time": 2}]  # tag 2: a real profit
        account = {"balances": [{"asset": "USDT", "free": "1000", "locked": "0"}]}
        tag4 = summarize_pilot(account, [], orders, {}, self.CONFIG, 0, tag="4")
        tag2 = summarize_pilot(account, [], orders, {}, self.CONFIG, 0, tag="2")
        self.assertLess(tag4["equity"], Decimal("17"))   # tag 4 lost money
        self.assertGreater(tag2["equity"], Decimal("17"))  # tag 2 made money

    def test_free_usdt_is_capped_by_this_tags_own_remaining_allocation(self):
        # Real account has plenty free (1000), but tag "4"'s own allocation
        # (17) minus what it already has committed to an open position (12)
        # leaves only 5 -- free_usdt must reflect THAT, not the account's
        # real 1000.
        orders = [{"side": "BUY", "status": "FILLED", "clientOrderId": "kv1b41",
                   "cummulativeQuoteQty": "12", "time": 1}]
        open_orders = [{"symbol": "SOLUSDT", "clientOrderId": "kv1s41"}]
        account = {"balances": [{"asset": "USDT", "free": "1000", "locked": "0"}]}
        result = summarize_pilot(account, open_orders, orders, {}, self.CONFIG, 0, tag="4")
        self.assertEqual(result["free_usdt"], Decimal("5"))

    def test_untagged_call_is_unaffected_by_tagged_orders_in_the_same_history(self):
        # Backward compatibility: tag="" (the default) must behave exactly
        # as if tags did not exist at all, seeing every "kv1"-prefixed order.
        orders = [{"side": "BUY", "status": "FILLED", "clientOrderId": "kv1b41",
                   "cummulativeQuoteQty": "10", "time": 1},
                  {"side": "SELL", "status": "FILLED", "clientOrderId": "kv1x41",
                   "cummulativeQuoteQty": "9", "time": 2}]
        account = {"balances": [{"asset": "USDT", "free": "50", "locked": "0"}]}
        result = summarize_pilot(account, [], orders, {}, {"pilot_capital_usdt": 34}, 0)
        self.assertEqual(result["equity"], Decimal("50"))  # real account balance, untouched
        self.assertEqual(result["buys_today"], 1)
