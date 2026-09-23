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


class LeveragedLongPilotAccountingTests(unittest.TestCase):
    """Leveraged long shares the same Futures wallet as short (21 Sep
    2026) but its open/close sides are REVERSED (open=BUY/kv1fl,
    close=SELL/kv1fq|kv1fk|kv1fy) -- a regression here would misfile a
    long open as an unmatched short close (both use BUY... no: a long's
    OPEN is BUY, same side as a short's CLOSE, so without prefix-exact
    matching a long open could be silently swallowed by the short-close
    filter and produce nonsense P&L)."""

    def test_a_profitable_long_is_paired_by_suffix_not_confused_with_a_short(self):
        orders = [
            {"symbol": "ADAUSDT", "clientOrderId": "kv1fl1", "side": "BUY", "status": "FILLED",
             "cumQuote": "100", "updateTime": 5000},
            {"symbol": "ADAUSDT", "clientOrderId": "kv1fq1", "side": "SELL", "status": "FILLED",
             "cumQuote": "110", "updateTime": 6000},
        ]
        result = summarize_short_pilot({}, [], orders, C, day_start_ms=0)
        self.assertEqual(result["realized_loss_today"], Decimal("0"))

    def test_a_losing_long_counts_toward_realized_loss(self):
        orders = [
            {"symbol": "ADAUSDT", "clientOrderId": "kv1fl1", "side": "BUY", "status": "FILLED",
             "cumQuote": "110", "updateTime": 5000},
            {"symbol": "ADAUSDT", "clientOrderId": "kv1fq1", "side": "SELL", "status": "FILLED",
             "cumQuote": "100", "updateTime": 6000},
        ]
        result = summarize_short_pilot({}, [], orders, C, day_start_ms=0)
        self.assertGreater(result["realized_loss_today"], Decimal("9.9"))

    def test_realized_loss_counts_the_normal_automatic_long_exit_too(self):
        # kv1fy mirrors kv1fx (the short side's normal trend/emergency-risk
        # exit prefix) for the long side.
        orders = [
            {"symbol": "ADAUSDT", "clientOrderId": "kv1fl1", "side": "BUY", "status": "FILLED",
             "cumQuote": "110", "updateTime": 5000},
            {"symbol": "ADAUSDT", "clientOrderId": "kv1fy1", "side": "SELL", "status": "FILLED",
             "cumQuote": "100", "updateTime": 6000},
        ]
        result = summarize_short_pilot({}, [], orders, C, day_start_ms=0)
        self.assertGreater(result["realized_loss_today"], Decimal("9.9"))

    def test_a_simultaneous_long_and_short_are_both_counted_not_confused(self):
        orders = [
            # Profitable short: sold 100, bought back 90.
            {"symbol": "SOLUSDT", "clientOrderId": "kv1fs41", "side": "SELL", "status": "FILLED",
             "cumQuote": "100", "updateTime": 5000},
            {"symbol": "SOLUSDT", "clientOrderId": "kv1fp41", "side": "BUY", "status": "FILLED",
             "cumQuote": "90", "updateTime": 6000},
            # Profitable long: bought 100, sold 110.
            {"symbol": "ADAUSDT", "clientOrderId": "kv1fl42", "side": "BUY", "status": "FILLED",
             "cumQuote": "100", "updateTime": 5000},
            {"symbol": "ADAUSDT", "clientOrderId": "kv1fq42", "side": "SELL", "status": "FILLED",
             "cumQuote": "110", "updateTime": 6000},
        ]
        account = {"availableBalance": "1000", "totalMarginBalance": "1000"}
        result = summarize_short_pilot(account, [], orders, {"pilot_capital_usdt": 17,
                                                              "live_fee_buffer_fraction": 0.001}, 0, tag="4")
        short_pnl = Decimal("100") * Decimal("0.999") - Decimal("90") * Decimal("1.001")
        long_pnl = Decimal("110") * Decimal("0.999") - Decimal("100") * Decimal("1.001")
        self.assertEqual(result["equity"], Decimal("17") + short_pnl + long_pnl)
        self.assertEqual(result["realized_loss_today"], Decimal("0"))

    def test_held_symbols_and_committed_capital_combine_both_sides(self):
        open_orders = [
            {"symbol": "SOLUSDT", "clientOrderId": "kv1fp41"},   # short's protective stop
            {"symbol": "ADAUSDT", "clientOrderId": "kv1fq42"},   # long's protective stop
        ]
        orders = [
            {"symbol": "SOLUSDT", "clientOrderId": "kv1fs41", "side": "SELL", "status": "FILLED",
             "cumQuote": "50", "updateTime": 1},
            {"symbol": "ADAUSDT", "clientOrderId": "kv1fl42", "side": "BUY", "status": "FILLED",
             "cumQuote": "30", "updateTime": 1},
        ]
        result = summarize_short_pilot({"availableBalance": "1000"}, open_orders, orders, C, 0, tag="4")
        self.assertEqual(result["open_positions"], 2)
        self.assertEqual(result["held_symbols"], {"SOLUSDT", "ADAUSDT"})
        # No positions list on the account -> leverage unknown -> the
        # conservative notional count: equity(34) - committed(50+30=80) -> floored at 0.
        self.assertEqual(result["free_usdt"], Decimal("0"))

    def test_committed_capital_is_the_margin_not_the_whole_notional(self):
        # 22 Sep 2026: counting notional let ONE ~70-90 USDT position use
        # up a 34 USDT slice, so free_usdt hit 0 after a single open and
        # max_open_positions was unreachable. The account's own positions
        # list carries each symbol's leverage.
        open_orders = [
            {"symbol": "SOLUSDT", "clientOrderId": "kv1fp41"},
            {"symbol": "ADAUSDT", "clientOrderId": "kv1fq42"},
        ]
        orders = [
            {"symbol": "SOLUSDT", "clientOrderId": "kv1fs41", "side": "SELL", "status": "FILLED",
             "cumQuote": "50", "updateTime": 1},
            {"symbol": "ADAUSDT", "clientOrderId": "kv1fl42", "side": "BUY", "status": "FILLED",
             "cumQuote": "30", "updateTime": 1},
        ]
        account = {"availableBalance": "1000",
                   "positions": [{"symbol": "SOLUSDT", "leverage": "2"},
                                 {"symbol": "ADAUSDT", "leverage": "3"}]}
        result = summarize_short_pilot(account, open_orders, orders, dict(C, pilot_capital_usdt=100), 0, tag="4")
        # committed = 50/2 + 30/3 = 35 -> free = 100 - 35
        self.assertEqual(result["free_usdt"], Decimal("65"))


class LivePositionsTests(unittest.TestCase):
    """live_positions() must tell a long and a short apart (21 Sep 2026):
    a short's protective stop is kv1fp/BUY, a long's is kv1fq/SELL --
    each tagged with its own "side" so the caller applies the right exit
    logic and executor calls."""

    def _market(self, executor, unprotected=()):
        market = BinanceFuturesMarket(C, {}, {}, executor)
        # Adoption of stopless positions has its own tests below; these
        # cover only what the protective stops themselves reveal.
        market.unprotected_positions = lambda protected=(): list(unprotected)
        return market

    def test_recognizes_a_short_stop(self):
        executor = MagicMock()
        executor.open_orders.return_value = [
            {"symbol": "SOLUSDT", "clientOrderId": "kv1fp1", "side": "BUY",
             "stopPrice": "105", "origQty": "1"}]
        executor.query.return_value = {"executedQty": "1", "cumQuote": "100", "time": 1}
        positions = self._market(executor).live_positions()
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["side"], "short")
        self.assertEqual(positions[0]["symbol"], "SOLUSDT")
        executor.query.assert_called_once_with("SOLUSDT", "kv1fs1")

    def test_recognizes_a_long_stop(self):
        executor = MagicMock()
        executor.open_orders.return_value = [
            {"symbol": "ADAUSDT", "clientOrderId": "kv1fq2", "side": "SELL",
             "stopPrice": "0.20", "origQty": "50"}]
        executor.query.return_value = {"executedQty": "50", "cumQuote": "10", "time": 1}
        positions = self._market(executor).live_positions()
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["side"], "long")
        self.assertEqual(positions[0]["symbol"], "ADAUSDT")
        executor.query.assert_called_once_with("ADAUSDT", "kv1fl2")

    def test_ignores_orders_that_are_neither_a_short_nor_a_long_stop(self):
        executor = MagicMock()
        executor.open_orders.return_value = [
            {"symbol": "SOLUSDT", "clientOrderId": "kv1fs1", "side": "SELL"},  # the open itself, not a stop
            {"symbol": "SOLUSDT", "clientOrderId": "kv1fp1", "side": "SELL"},  # wrong side for a short stop
            {"symbol": "ADAUSDT", "clientOrderId": "kv1fq2", "side": "BUY"},   # wrong side for a long stop
        ]
        self.assertEqual(self._market(executor).live_positions(), [])
        executor.query.assert_not_called()

    def test_returns_both_a_long_and_a_short_together(self):
        executor = MagicMock()
        executor.open_orders.return_value = [
            {"symbol": "SOLUSDT", "clientOrderId": "kv1fp1", "side": "BUY",
             "stopPrice": "105", "origQty": "1"},
            {"symbol": "ADAUSDT", "clientOrderId": "kv1fq2", "side": "SELL",
             "stopPrice": "0.20", "origQty": "50"},
        ]
        executor.query.return_value = {"executedQty": "1", "cumQuote": "10", "time": 1}
        positions = self._market(executor).live_positions()
        self.assertEqual({p["side"] for p in positions}, {"short", "long"})


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

    def test_a_long_position_uses_high_since_entry_not_low(self):
        # Leveraged long (21 Sep 2026): trailing needs the running HIGH,
        # same math as live_market.BinanceMarket.analysis's spot long --
        # opposite of the default (side-less/short) low-tracking above.
        interval = self.INTERVAL
        rows = self._rows([999 * interval, 1000 * interval, 1001 * interval], [10, 20, 15])
        position = {"symbol": "ADAUSDT", "side": "long", "open_time": 1000 * interval + 1}
        with patch("crypto_v1.live_short_market.get", return_value={"serverTime": 1002 * interval}), \
             patch("crypto_v1.live_short_market.candles", return_value=rows):
            _, _, high = self._market().analysis(
                position, feature_fn=lambda rows, c: [dict(r) for r in rows], interval=interval)
        # _rows sets h = l+1, so bars closed since entry have h in {21, 16}.
        self.assertEqual(high, 21)


def _contract(symbol, tick, step, notional):
    return {"symbol": symbol, "filters": [
        {"filterType": "PRICE_FILTER", "tickSize": tick},
        {"filterType": "LOT_SIZE", "stepSize": step, "minQty": step},
        {"filterType": "MIN_NOTIONAL", "notional": notional}]}


class RulesTests(unittest.TestCase):
    """Futures exchangeInfo ignores `symbol` and returns every contract,
    BTCUSDT first (live-verified 22 Sep 2026): rules() must pick the
    requested symbol, not symbols[0]."""

    def _market(self, symbols):
        executor = MagicMock()
        executor.request.return_value = {"symbols": symbols}
        return BinanceFuturesMarket(C, {}, {}, executor)

    def test_picks_the_requested_symbol_not_the_first(self):
        market = self._market([_contract("BTCUSDT", "0.10", "0.001", "50"),
                               _contract("ADAUSDT", "0.00010", "1", "5")])
        rules = market.rules("ADAUSDT")
        self.assertEqual(rules["tick_size"], Decimal("0.0001"))
        self.assertEqual(rules["step_size"], Decimal("1"))
        self.assertEqual(rules["min_notional"], Decimal("5"))

    def test_unlisted_symbol_is_a_planning_rejection(self):
        market = self._market([_contract("BTCUSDT", "0.10", "0.001", "50")])
        with self.assertRaises(ValueError):
            market.rules("ADAUSDT")


class PriceTests(unittest.TestCase):
    """positionRisk reports markPrice "0" when no position is open (live 22
    Sep 2026), which rejected every new entry: price must come from the
    public premiumIndex, and a zero must never be returned."""

    def test_uses_the_public_mark_price_not_position_risk(self):
        executor = MagicMock()
        with patch("crypto_v1.live_short_market.futures_get",
                   return_value={"symbol": "BCHUSDT", "markPrice": "326.37"}) as get:
            price = BinanceFuturesMarket(C, {}, {}, executor).price("BCHUSDT")
        self.assertEqual(price, Decimal("326.37"))
        get.assert_called_once_with("premiumIndex", {"symbol": "BCHUSDT"})
        executor.position_risk.assert_not_called()

    def test_zero_mark_price_fails_instead_of_sizing(self):
        with patch("crypto_v1.live_short_market.futures_get", return_value={"markPrice": "0"}):
            with self.assertRaises(RuntimeError):
                BinanceFuturesMarket(C, {}, {}, MagicMock()).price("BCHUSDT")


class UnprotectedPositionTests(unittest.TestCase):
    """22 Sep 2026: every protective stop failed to place (Binance -4120),
    so a real open position was invisible to may_open and the same symbol
    was bought again. Exchange positions now count as held and committed."""

    def _summary(self, positions, open_orders=()):
        account = {"availableBalance": "100", "totalMarginBalance": "100", "positions": positions}
        return summarize_short_pilot(account, list(open_orders), [], dict(C, pilot_capital_usdt=100), 0, tag="4")

    def test_a_position_without_a_stop_still_counts_as_held(self):
        result = self._summary([{"symbol": "BCHUSDT", "positionAmt": "0.26", "leverage": "4",
                                 "positionInitialMargin": "21"}])
        self.assertEqual(result["held_symbols"], {"BCHUSDT"})
        self.assertEqual(result["open_positions"], 1)
        self.assertEqual(result["free_usdt"], Decimal("79"))  # 100 - 21 margin

    def test_a_short_position_counts_too(self):
        result = self._summary([{"symbol": "SOLUSDT", "positionAmt": "-3", "leverage": "3",
                                 "positionInitialMargin": "10"}])
        self.assertEqual(result["held_symbols"], {"SOLUSDT"})

    def test_a_flat_symbol_is_not_held(self):
        result = self._summary([{"symbol": "ADAUSDT", "positionAmt": "0", "leverage": "4"}])
        self.assertEqual(result["held_symbols"], set())
        self.assertEqual(result["free_usdt"], Decimal("100"))


class UnprotectedPositionAdoptionTests(unittest.TestCase):
    """A position whose protective stop never got placed (Binance -4120, 22
    Sep 2026) was invisible to the exit loop. It is now adopted by the
    system whose opening order it carries."""

    def _market(self, positions, orders, tag="4"):
        market = BinanceFuturesMarket(C, {}, {}, MagicMock(), tag=tag)
        market._raw_pilot_data = lambda: ({"positions": positions}, [], orders)
        return market

    OPEN_4H = {"symbol": "BCHUSDT", "clientOrderId": "kv1fl41790107200000",
               "status": "FILLED", "side": "BUY", "time": 1790107200000}

    def test_adopts_a_long_opened_by_this_system(self):
        market = self._market([{"symbol": "BCHUSDT", "positionAmt": "0.261", "entryPrice": "336"}],
                              [self.OPEN_4H])
        position = market.unprotected_positions()[0]
        self.assertEqual(position["side"], "long")
        self.assertEqual(position["quantity"], "0.261")
        self.assertEqual(position["entry"], Decimal("336"))
        self.assertTrue(position["unprotected"])
        self.assertIsNone(position["stop_price"])
        # Pairs with its opening order's suffix, so P&L accounting still matches.
        self.assertEqual(position["stop_client_id"], "kv1fq41790107200000")

    def test_leaves_the_other_system_s_position_alone(self):
        market = self._market([{"symbol": "BCHUSDT", "positionAmt": "0.261", "entryPrice": "336"}],
                              [self.OPEN_4H], tag="2")
        self.assertEqual(market.unprotected_positions(), [])

    def test_leaves_a_hand_opened_position_alone(self):
        market = self._market([{"symbol": "BCHUSDT", "positionAmt": "0.261", "entryPrice": "336"}], [])
        self.assertEqual(market.unprotected_positions(), [])

    def test_skips_a_position_that_already_has_a_stop(self):
        market = self._market([{"symbol": "BCHUSDT", "positionAmt": "0.261", "entryPrice": "336"}],
                              [self.OPEN_4H])
        self.assertEqual(market.unprotected_positions({"BCHUSDT"}), [])

    def test_a_short_is_adopted_with_its_own_prefixes(self):
        opened = {"symbol": "SOLUSDT", "clientOrderId": "kv1fs41790107200000",
                  "status": "FILLED", "side": "SELL", "time": 1790107200000}
        market = self._market([{"symbol": "SOLUSDT", "positionAmt": "-3", "entryPrice": "100"}], [opened])
        position = market.unprotected_positions()[0]
        self.assertEqual(position["side"], "short")
        self.assertEqual(position["quantity"], "3")
        self.assertEqual(position["stop_client_id"], "kv1fp41790107200000")

    def test_a_flat_symbol_is_not_adopted(self):
        market = self._market([{"symbol": "BCHUSDT", "positionAmt": "0", "entryPrice": "336"}],
                              [self.OPEN_4H])
        self.assertEqual(market.unprotected_positions(), [])


if __name__ == '__main__':
    unittest.main()
