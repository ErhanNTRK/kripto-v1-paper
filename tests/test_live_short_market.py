import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch

from crypto_v1.live_short_market import BinanceFuturesMarket, summarize_short_pilot


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
        self.assertEqual(result["held_symbols"], {"SOLUSDT", "ETHUSDT"})

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

    def test_realized_loss_counts_the_normal_automatic_exit_too(self):
        # kv1fx is live_short_monitor.execute_short_exit's prefix for the
        # ordinary automatic trend/emergency-risk close -- the most common
        # real exit path, not just the protective stop (kv1fp) or the
        # controller's own emergency close (kv1fe). A regression here would
        # silently exclude most real losses from the daily-loss breaker.
        orders = [
            {"symbol": "SOLUSDT", "clientOrderId": "kv1fs1", "side": "SELL", "status": "FILLED",
             "cumQuote": "100", "updateTime": 5000},
            {"symbol": "SOLUSDT", "clientOrderId": "kv1fx1", "side": "BUY", "status": "FILLED",
             "cumQuote": "110", "updateTime": 6000},
        ]
        result = summarize_short_pilot({}, [], orders, C, day_start_ms=0)
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


class TaggedSubSystemTests(unittest.TestCase):
    """Two systems (e.g. "4" for 4H, "2" for 2H) share the SAME real
    Futures wallet -- each must only see and spend its own slice of
    capital, tracked via its own all-time realized P&L, never the whole
    account's real margin balance."""

    CONFIG = {"pilot_capital_usdt": 17, "live_fee_buffer_fraction": 0.001}

    def test_tag_equity_comes_from_its_own_realized_pnl_not_the_account_balance(self):
        orders = [
            {"clientOrderId": "kv1fs41", "side": "SELL", "status": "FILLED",
             "cumQuote": "110", "updateTime": 1},
            {"clientOrderId": "kv1fp41", "side": "BUY", "status": "FILLED",
             "cumQuote": "100", "updateTime": 2},
        ]
        account = {"availableBalance": "1000", "totalMarginBalance": "1000"}
        result = summarize_short_pilot(account, [], orders, self.CONFIG, 0, tag="4")
        pnl = Decimal("110") * Decimal("0.999") - Decimal("100") * Decimal("1.001")
        self.assertEqual(result["equity"], Decimal("17") + pnl)
        self.assertGreater(result["equity"], Decimal("17"))  # this trade was a real profit

    def test_orders_from_a_different_tag_never_leak_in(self):
        orders = [
            {"clientOrderId": "kv1fs41", "side": "SELL", "status": "FILLED",
             "cumQuote": "100", "updateTime": 1},
            {"clientOrderId": "kv1fp41", "side": "BUY", "status": "FILLED",
             "cumQuote": "110", "updateTime": 2},  # tag 4: a real loss
            {"clientOrderId": "kv1fs21", "side": "SELL", "status": "FILLED",
             "cumQuote": "100", "updateTime": 1},
            {"clientOrderId": "kv1fp21", "side": "BUY", "status": "FILLED",
             "cumQuote": "50", "updateTime": 2},   # tag 2: a real profit
        ]
        account = {"availableBalance": "1000", "totalMarginBalance": "1000"}
        tag4 = summarize_short_pilot(account, [], orders, self.CONFIG, 0, tag="4")
        tag2 = summarize_short_pilot(account, [], orders, self.CONFIG, 0, tag="2")
        self.assertLess(tag4["equity"], Decimal("17"))
        self.assertGreater(tag2["equity"], Decimal("17"))

    def test_free_usdt_is_capped_by_this_tags_own_remaining_allocation(self):
        orders = [{"clientOrderId": "kv1fs41", "side": "SELL", "status": "FILLED",
                   "cumQuote": "12", "updateTime": 1}]
        open_orders = [{"symbol": "SOLUSDT", "clientOrderId": "kv1fp41"}]
        account = {"availableBalance": "1000", "totalMarginBalance": "1000"}
        result = summarize_short_pilot(account, open_orders, orders, self.CONFIG, 0, tag="4")
        self.assertEqual(result["free_usdt"], Decimal("5"))

    def test_untagged_call_uses_the_real_account_balance(self):
        account = {"availableBalance": "30.5", "totalMarginBalance": "33.2"}
        result = summarize_short_pilot(account, [], [], self.CONFIG, 0)
        self.assertEqual(result["equity"], Decimal("33.2"))
        self.assertEqual(result["free_usdt"], Decimal("30.5"))


class FuturesPilotStatusCacheTests(unittest.TestCase):
    """Mirrors live_market's PilotStatusCacheTests: pilot_status() fans an
    all_orders() call out to every symbol in the scanning universe, so a
    short TTL cache should collapse near-simultaneous calls into one
    real fetch (see BinanceFuturesMarket's cache docstring)."""

    def setUp(self):
        BinanceFuturesMarket._raw_pilot_cache = None  # class-level, shared: never leak between tests

    def _market(self, now_fn, tag="", executor=None):
        executor = executor or MagicMock()
        executor.open_orders.return_value = []
        executor.all_orders.return_value = []
        executor.account.return_value = {"availableBalance": "0", "totalMarginBalance": "0"}
        market = BinanceFuturesMarket(C, {"top_n": 1, "excluded_bases": []}, {}, executor,
                                      tag=tag, now=now_fn)
        return market, executor

    @patch("crypto_v1.live_short_market.universe", return_value=["BTCUSDT"])
    def test_second_call_within_ttl_reuses_cached_fetch(self, _mock_universe):
        clock = [1_000.0]
        market, executor = self._market(lambda: clock[0])
        first = market.pilot_status()
        clock[0] += 10
        second = market.pilot_status()
        self.assertEqual(executor.all_orders.call_count, 1)
        self.assertEqual(first, second)

    @patch("crypto_v1.live_short_market.universe", return_value=["BTCUSDT"])
    def test_the_4h_and_2h_apps_share_one_fetch(self, _mock_universe):
        clock = [1_000.0]
        executor = MagicMock()
        four_h, _ = self._market(lambda: clock[0], tag="4", executor=executor)
        two_h, _ = self._market(lambda: clock[0], tag="2", executor=executor)
        four_h.pilot_status()
        two_h.pilot_status()
        self.assertEqual(executor.all_orders.call_count, 1)

    @patch("crypto_v1.live_short_market.universe", return_value=["BTCUSDT"])
    def test_call_after_ttl_expires_refetches(self, _mock_universe):
        clock = [1_000.0]
        market, executor = self._market(lambda: clock[0])
        market.pilot_status()
        clock[0] += 91
        market.pilot_status()
        self.assertEqual(executor.all_orders.call_count, 2)


class AnalysisTests(unittest.TestCase):
    """Mirrors test_live_market.AnalysisTests for the short side's "low
    since entry" computation -- same 21 Sep 2026 fix, same reasoning: a
    bar's "t" is its OPEN time, but the candle used for entry CLOSES at
    floor(open_time, interval), one interval after its own open."""

    INTERVAL = 7_200_000  # 2h

    @staticmethod
    def _rows(times, lows):
        return [dict(t=t, o=l, h=l + 1, l=l, c=l, v=1) for t, l in zip(times, lows)]

    @staticmethod
    def _market():
        market = BinanceFuturesMarket.__new__(BinanceFuturesMarket)
        market.strategy_config = {}
        return market

    def test_falls_back_to_the_entry_candles_own_low_when_nothing_closed_since(self):
        interval = self.INTERVAL
        now = 1000 * interval
        rows = self._rows([now - 3 * interval, now - 2 * interval, now - interval], [10, 9, 8])
        position = {"symbol": "BNBUSDT", "open_time": now + 1}
        with patch("crypto_v1.live_short_market.get", return_value={"serverTime": now}), \
             patch("crypto_v1.live_short_market.candles", return_value=rows):
            _, _, low = self._market().analysis(
                position, feature_fn=lambda rows, c: [dict(r) for r in rows], interval=interval)
        self.assertEqual(low, 8)  # the entry candle's own low, not a crash

    def test_uses_the_real_post_entry_low_once_a_candle_has_closed_since(self):
        interval = self.INTERVAL
        rows = self._rows([999 * interval, 1000 * interval, 1001 * interval], [10, 5, 7])
        position = {"symbol": "BNBUSDT", "open_time": 1000 * interval + 1}
        with patch("crypto_v1.live_short_market.get", return_value={"serverTime": 1002 * interval}), \
             patch("crypto_v1.live_short_market.candles", return_value=rows):
            _, _, low = self._market().analysis(
                position, feature_fn=lambda rows, c: [dict(r) for r in rows], interval=interval)
        self.assertEqual(low, 5)  # min of bars closed since entry


if __name__ == '__main__':
    unittest.main()
