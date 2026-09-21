import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch
from crypto_v1.live_market import BinanceMarket, summarize_pilot


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


class AnalysisTests(unittest.TestCase):
    """analysis()'s trailing "high since entry" computation, regression-
    covered 21 Sep 2026 after it crashed live for a real just-opened
    position: "Spot exit scan failed: max() iterable argument is empty".
    A bar's "t" is its OPEN time, but the candle used for entry CLOSES at
    floor(buy_time, interval) -- its own open time is one interval
    earlier -- so filtering for t >= that floor excludes the entry candle
    itself and finds nothing until a further candle has closed."""

    INTERVAL = 7_200_000  # 2h

    @staticmethod
    def _rows(times, highs):
        return [dict(t=t, o=h, h=h, l=h - 1, c=h, v=1) for t, h in zip(times, highs)]

    @staticmethod
    def _market():
        market = BinanceMarket.__new__(BinanceMarket)
        market.strategy_config = {}
        return market

    def test_falls_back_to_the_entry_candles_own_high_when_nothing_closed_since(self):
        interval = self.INTERVAL
        now = 1000 * interval
        rows = self._rows([now - 3 * interval, now - 2 * interval, now - interval], [10, 11, 12])
        position = {"symbol": "ADAUSDT", "buy_time": now + 1}  # bought right after "now"'s boundary
        with patch("crypto_v1.live_market.get", return_value={"serverTime": now}), \
             patch("crypto_v1.live_market.candles", return_value=rows):
            _, _, high = self._market().analysis(
                position, feature_fn=lambda rows, c: [dict(r) for r in rows], interval=interval)
        self.assertEqual(high, 12)  # the entry candle's own high, not a crash

    def test_uses_the_real_post_entry_high_once_a_candle_has_closed_since(self):
        interval = self.INTERVAL
        rows = self._rows([999 * interval, 1000 * interval, 1001 * interval], [10, 20, 15])
        position = {"symbol": "ADAUSDT", "buy_time": 1000 * interval + 1}
        with patch("crypto_v1.live_market.get", return_value={"serverTime": 1002 * interval}), \
             patch("crypto_v1.live_market.candles", return_value=rows):
            _, _, high = self._market().analysis(
                position, feature_fn=lambda rows, c: [dict(r) for r in rows], interval=interval)
        self.assertEqual(high, 20)  # max of bars closed since entry (1000*interval, 1001*interval)


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


class PilotStatusCacheTests(unittest.TestCase):
    """pilot_status() fans an all_orders() call out to every symbol in the
    scanning universe (~20 Binance weight each) -- live-observed 18 Sep
    2026 as a real contributor to repeated -1003 rate-limit bans when
    several near-simultaneous callers (multiple pending candidates, two
    tagged systems) each trigger a fresh fetch within seconds. A short TTL
    cache should collapse those into one real fetch."""

    BALANCES = [{"asset": "USDT", "free": "17", "locked": "0"},
                {"asset": "SOL", "free": "0.001", "locked": "0"}]   # dust from a past trade
    TICKERS = {"SOLUSDT": Decimal("100"), "BTCUSDT": Decimal("50000")}

    def setUp(self):
        BinanceMarket._raw_pilot_cache = None  # class-level, shared: never leak between tests

    def _market(self, now_fn, tag="", executor=None):
        executor = executor or MagicMock()
        executor.open_orders.return_value = []
        executor.all_orders.return_value = []
        market = BinanceMarket({"pilot_capital_usdt": 17}, {"top_n": 50, "excluded_bases": []},
                               {}, executor, tag=tag, now=now_fn)
        market._account = MagicMock(return_value={"balances": self.BALANCES})
        market._tickers = MagicMock(return_value=self.TICKERS)
        return market, executor

    def test_second_call_within_ttl_reuses_cached_fetch(self):
        clock = [1_000.0]
        market, executor = self._market(lambda: clock[0])
        first = market.pilot_status()
        clock[0] += 10  # well inside the 90s TTL
        second = market.pilot_status()
        self.assertEqual(executor.all_orders.call_count, 1)  # not refetched
        self.assertEqual(first, second)

    def test_call_after_ttl_expires_refetches(self):
        clock = [1_000.0]
        market, executor = self._market(lambda: clock[0])
        market.pilot_status()
        clock[0] += 91  # past the 90s TTL
        market.pilot_status()
        self.assertEqual(executor.all_orders.call_count, 2)

    def test_the_4h_and_2h_apps_share_one_fetch(self):
        # Two LiveApps (tags "4" and "2") each own a BinanceMarket over the
        # SAME wallet; the raw fetch is tag-agnostic, so the second app must
        # reuse the first app's fetch instead of re-running the whole scan.
        clock = [1_000.0]
        executor = MagicMock()
        four_h, _ = self._market(lambda: clock[0], tag="4", executor=executor)
        two_h, _ = self._market(lambda: clock[0], tag="2", executor=executor)
        four_h.pilot_status()
        two_h.pilot_status()
        self.assertEqual(executor.all_orders.call_count, 1)
        self.assertEqual(executor.open_orders.call_count, 1)

    def test_only_held_and_open_order_symbols_are_scanned_not_the_whole_universe(self):
        clock = [1_000.0]
        market, executor = self._market(lambda: clock[0])
        executor.open_orders.return_value = [{"symbol": "ETHUSDT", "clientOrderId": "kv1s41"}]
        market.pilot_status()
        scanned = {call.args[0] for call in executor.all_orders.call_args_list}
        # SOL: dust balance; ETH: open protective stop. BTC is in tickers but
        # neither held nor ordered -- no history to find there, no call made.
        self.assertEqual(scanned, {"SOLUSDT", "ETHUSDT"})
