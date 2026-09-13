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
                                 {"pilot_capital_usdt": 68.13})
        self.assertEqual(result["open_positions"], 1)
        self.assertEqual(result["buys_today"], 1)
        self.assertEqual(result["realized_loss_today"], Decimal("6.8"))
        self.assertEqual(result["equity"], Decimal("65"))
        self.assertEqual(result["pilot_drawdown"], Decimal("3.13"))

    def test_completed_round_trip_counts_realized_loss(self):
        orders = [{"side": "BUY", "status": "FILLED", "clientOrderId": "kv1b1",
                   "cummulativeQuoteQty": "10"},
                  {"side": "SELL", "status": "FILLED", "clientOrderId": "kv1x1",
                   "cummulativeQuoteQty": "9.7"}]
        result = summarize_pilot({"balances": [{"asset": "USDT", "free": "67.83",
                                                 "locked": "0"}]}, [], orders, {},
                                 {"pilot_capital_usdt": 68.13})
        self.assertEqual(result["realized_loss_today"], Decimal("0.3"))
