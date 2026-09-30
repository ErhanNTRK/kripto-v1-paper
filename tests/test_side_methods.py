import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from crypto_v1.binance_trade import OrderRejected
from crypto_v1.side_methods import DAY_MS, FOUR_HOUR_MS, SignalTrader, daily_cup, short_cup


def bars(closes, t0=0, step=DAY_MS, lows=None):
    return [{"t": t0 + i * step, "o": c, "h": c * 1.01, "l": (lows or {}).get(i, c * 0.99), "c": c}
            for i, c in enumerate(closes)]


def btc_up(n=260):
    return bars([100 + i * 0.1 for i in range(n)])


class DetectorTests(unittest.TestCase):
    def _cup(self, depth_low=70.0, trough_at=100, breakout=12.0):
        # flat 10, rim 20 at day 60 (the 120-day high), fall to the trough, recover, break out on the last day
        closes = [10.0] * 60 + [20.0] + [15.0] * 60 + [19.5, 20.5]
        lows = {trough_at: depth_low * 20 / 100}
        return bars(closes, lows=lows)

    def test_daily_cup_breakout_is_found(self):
        c = daily_cup(self._cup(depth_low=70.0), btc_up())      # 30% deep
        self.assertIsNotNone(c)
        self.assertEqual(c["close"], 20.5)

    def test_daily_cup_needs_the_right_depth_and_a_rising_btc(self):
        self.assertIsNone(daily_cup(self._cup(depth_low=95.0), btc_up()))     # only ~5% deep
        falling = bars([200 - i * 0.1 for i in range(260)])
        self.assertIsNone(daily_cup(self._cup(depth_low=70.0), falling))

    def test_short_cup_breakout_is_found_and_depth_is_bounded(self):
        closes = [10.0] * 60 + [12.0] + [11.0] * 20 + [11.8, 12.2]           # rim at bar 60
        ok = short_cup(bars(closes, step=FOUR_HOUR_MS, lows={70: 10.5}))    # 12.5% deep
        self.assertIsNotNone(ok)
        too_deep = short_cup(bars(closes, step=FOUR_HOUR_MS, lows={70: 8.0}))
        self.assertIsNone(too_deep)
        no_break = short_cup(bars(closes[:-1] + [11.9], step=FOUR_HOUR_MS, lows={70: 10.5}))
        self.assertIsNone(no_break)


class FakeExecutor:
    def __init__(self):
        self.amounts, self.open, self.calls = {}, {}, []

    def account(self):
        return {"positions": [{"symbol": s, "positionAmt": str(a)} for s, a in self.amounts.items()]}

    def open_orders(self):
        return [{"clientOrderId": cid, "symbol": s} for cid, s in self.open.items()]

    def set_isolated_margin(self, symbol):
        pass

    def set_leverage(self, symbol, lev):
        self.calls.append(("leverage", symbol, lev))

    def market_open_long(self, symbol, qty, cid):
        self.calls.append(("buy", symbol, qty, cid)); self.amounts[symbol] = Decimal(qty)
        return {"executedQty": qty, "avgPrice": "10"}

    def protective_stop_for_long(self, symbol, qty, stop, cid):
        self.calls.append(("stop", symbol, qty, stop, cid)); self.open[cid] = symbol

    def take_profit_for_long(self, symbol, qty, tp, cid):
        self.calls.append(("tp", symbol, qty, tp, cid)); self.open[cid] = symbol

    def cancel(self, symbol, cid):
        self.calls.append(("cancel", symbol, cid)); self.open.pop(cid, None)

    def market_close_long(self, symbol, qty, cid):
        self.calls.append(("close", symbol, qty, cid)); self.amounts[symbol] = Decimal("0")
        return {"avgPrice": "10.5"}

    def query(self, symbol, cid):
        raise OrderRejected(-2013)


class FakeMarket:
    def rules(self, symbol):
        return {"tick_size": Decimal("0.001"), "step_size": Decimal("0.1"), "min_qty": Decimal("0.1"),
                "min_notional": Decimal("5")}

    def price(self, symbol):
        return 10


class SignalTraderTests(unittest.TestCase):
    T = 1_790_755_200_000

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "sig.json").write_text(json.dumps({"window": self.T, "bar_ms": FOUR_HOUR_MS, "candidates": [
            {"symbol": "ASTERUSDT", "close": 10, "atr": 0.5, "time": self.T - FOUR_HOUR_MS}]}))
        self.ex, self.sent, self.now = FakeExecutor(), [], [self.T + 60_000]

    def _trader(self, config=None):
        cfg = config or {"mode": "live", "stop_atr": 2.0, "hold_hours": 24, "take_profit": 0.03,
                         "risk_fraction": 0.015, "max_age_minutes": 30}
        return SignalTrader(cfg, "KISA-CANAK", {"open": "kv1fa", "stop": "kv1fb", "tp": "kv1fi", "exit": "kv1ft"},
                            self.dir / "state.json", self.dir / "sig.json", self.ex, FakeMarket(),
                            send=self.sent.append, clock=lambda: self.now[0] / 1000, equity_fn=lambda: 100)

    def test_buys_with_stop_and_take_profit(self):
        state = self._trader().tick()
        buy = next(c for c in self.ex.calls if c[0] == "buy")
        self.assertEqual(buy[2], "1.5")                     # 1.5 USDT risk / (10 - 9) = 1.5
        self.assertTrue(buy[3].startswith("kv1fa"))
        stop = next(c for c in self.ex.calls if c[0] == "stop")
        tp = next(c for c in self.ex.calls if c[0] == "tp")
        self.assertEqual((Decimal(stop[3]), Decimal(tp[3])), (Decimal("9"), Decimal("10.3")))
        self.assertIn("ASTERUSDT", state["positions"])

    def test_take_profit_fired_is_recorded_and_the_stop_cancelled(self):
        t = self._trader()
        t.tick()
        self.ex.amounts["ASTERUSDT"] = Decimal("0")
        self.ex.open = {k: v for k, v in self.ex.open.items() if not k.startswith("kv1fi")}
        state = t.tick()
        self.assertEqual(state["closed"][0]["reason"], "hedef")
        self.assertTrue(any(c[0] == "cancel" and c[2].startswith("kv1fb") for c in self.ex.calls))

    def test_time_exit_and_old_or_busy_signals(self):
        t = self._trader()
        t.tick()
        self.now[0] += 24 * 3_600_000 + 1
        state = t.tick()
        self.assertEqual(state["closed"][0]["reason"], "sure doldu")
        self.setUp()
        self.now[0] = self.T + 45 * 60_000
        self._trader().tick()
        self.assertFalse(any(c[0] == "buy" for c in self.ex.calls))
        self.setUp()
        self.ex.amounts["ASTERUSDT"] = Decimal("5")          # held by hand
        self._trader().tick()
        self.assertFalse(any(c[0] == "buy" for c in self.ex.calls))

    def test_no_take_profit_when_not_configured(self):
        cfg = {"mode": "live", "stop_atr": 2.0, "hold_hours": 480, "risk_fraction": 0.015, "max_age_minutes": 30}
        self._trader(cfg).tick()
        self.assertFalse(any(c[0] == "tp" for c in self.ex.calls))
        self.assertTrue(any(c[0] == "stop" for c in self.ex.calls))


if __name__ == "__main__":
    unittest.main()
