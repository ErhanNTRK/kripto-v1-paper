import unittest
from decimal import Decimal

from crypto_v1.live_short_market import summarize_short_pilot


C = {"pilot_capital_usdt": 34, "live_fee_buffer_fraction": 0.001}


class SummarizeShortPilotTests(unittest.TestCase):
    def test_equity_and_free_usdt_come_from_the_futures_account(self):
        account = {"availableBalance": "30.5", "totalMarginBalance": "33.2"}
        result = summarize_short_pilot(account, [], [], C)
        self.assertEqual(result["free_usdt"], Decimal("30.5"))
        self.assertEqual(result["equity"], Decimal("33.2"))

    def test_pilot_drawdown_is_capital_minus_equity_floored_at_zero(self):
        account = {"availableBalance": "10", "totalMarginBalance": "30"}
        result = summarize_short_pilot(account, [], [], C)
        self.assertEqual(result["pilot_drawdown"], Decimal("4"))
        account_up = {"availableBalance": "10", "totalMarginBalance": "40"}
        self.assertEqual(summarize_short_pilot(account_up, [], [], C)["pilot_drawdown"], Decimal("0"))

    def test_open_positions_counts_distinct_symbols_with_a_protective_stop(self):
        open_orders = [
            {"symbol": "SOLUSDT", "clientOrderId": "kv1fp1"},
            {"symbol": "SOLUSDT", "clientOrderId": "kv1fs1"},  # not a stop, ignored
            {"symbol": "ETHUSDT", "clientOrderId": "kv1fp2"},
        ]
        result = summarize_short_pilot({}, open_orders, [], C)
        self.assertEqual(result["open_positions"], 2)

    def test_realized_loss_pairs_open_and_close_by_client_id_suffix(self):
        orders = [
            {"symbol": "SOLUSDT", "clientOrderId": "kv1fs1", "side": "SELL", "status": "FILLED",
             "cumQuote": "100", "updateTime": 5000},
            {"symbol": "SOLUSDT", "clientOrderId": "kv1fp1", "side": "BUY", "status": "FILLED",
             "cumQuote": "110", "updateTime": 6000},
        ]
        result = summarize_short_pilot({}, [], orders, C, day_start_ms=0)
        # short sold at 100, bought back at 110 -> a real loss, plus the fee buffer
        self.assertGreater(result["realized_loss_today"], Decimal("9.9"))

    def test_realized_loss_ignores_closes_before_today(self):
        orders = [
            {"symbol": "SOLUSDT", "clientOrderId": "kv1fs1", "side": "SELL", "status": "FILLED",
             "cumQuote": "100", "updateTime": 1000},
            {"symbol": "SOLUSDT", "clientOrderId": "kv1fp1", "side": "BUY", "status": "FILLED",
             "cumQuote": "110", "updateTime": 1000},
        ]
        result = summarize_short_pilot({}, [], orders, C, day_start_ms=5000)
        self.assertEqual(result["realized_loss_today"], Decimal("0"))

    def test_a_profitable_short_contributes_no_realized_loss(self):
        orders = [
            {"symbol": "SOLUSDT", "clientOrderId": "kv1fs1", "side": "SELL", "status": "FILLED",
             "cumQuote": "110", "updateTime": 5000},
            {"symbol": "SOLUSDT", "clientOrderId": "kv1fp1", "side": "BUY", "status": "FILLED",
             "cumQuote": "100", "updateTime": 6000},
        ]
        result = summarize_short_pilot({}, [], orders, C, day_start_ms=0)
        self.assertEqual(result["realized_loss_today"], Decimal("0"))


if __name__ == '__main__':
    unittest.main()
