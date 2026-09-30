import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from crypto_v1.binance_trade import OrderRejected
from crypto_v1.dip_catcher import DAY_MS
from crypto_v1.dip_live import LiveDipCatcher

DAY = 1_790_640_000_000 // DAY_MS * DAY_MS
CONFIG = {"mode": "live", "top_n": 2, "depth": 0.10, "take_profit": 0.03, "stop": 0.10,
          "hold_hours": 24, "size_fraction": 0.10, "btc_calm": 0.02}
RULES = {"tick_size": Decimal("0.01"), "step_size": Decimal("0.001"), "min_qty": Decimal("0.001"),
         "min_notional": Decimal("5")}


class FakeExecutor:
    def __init__(self):
        self.amounts, self.entry, self.orders, self.open, self.calls = {}, {}, {}, {}, []
        self.fail_stop = False
        self.close_price = {}

    def account(self):
        return {"positions": [{"symbol": s, "positionAmt": str(a), "entryPrice": str(self.entry.get(s, "0"))}
                              for s, a in self.amounts.items()]}

    def open_orders(self):
        return [{"clientOrderId": cid, "symbol": sym} for cid, sym in self.open.items()]

    def query(self, symbol, cid):
        if cid not in self.orders:
            raise OrderRejected(-2013)
        return self.orders[cid]

    def cancel(self, symbol, cid):
        self.calls.append(("cancel", symbol, cid))
        self.open.pop(cid, None)
        if cid in self.orders and self.orders[cid]["status"] == "NEW":
            self.orders[cid]["status"] = "CANCELED"
        return {"status": "CANCELED"}

    def set_isolated_margin(self, symbol):
        self.calls.append(("isolated", symbol))

    def set_leverage(self, symbol, leverage):
        self.calls.append(("leverage", symbol, leverage))

    def market_open_long(self, symbol, qty, cid):
        self.calls.append(("buy", symbol, qty, cid))
        self.amounts[symbol] = Decimal(qty)
        self.entry[symbol] = Decimal("90")
        return {"executedQty": qty, "avgPrice": "90"}

    def protective_stop_for_long(self, symbol, qty, stop, cid):
        self.calls.append(("stop", symbol, qty, stop, cid))
        if self.fail_stop:
            raise OrderRejected(-2021)
        self.open[cid] = symbol

    def take_profit_for_long(self, symbol, qty, tp, cid):
        self.calls.append(("tp", symbol, qty, tp, cid))
        self.open[cid] = symbol

    def market_close_long(self, symbol, qty, cid):
        self.calls.append(("close", symbol, qty, cid))
        self.amounts[symbol] = Decimal("0")
        return {"avgPrice": str(self.close_price.get(symbol, "88"))}


class FakeMarket:
    def rules(self, symbol):
        return RULES


class Klines:
    """Yesterday's daily close 100 for every coin; BTC's 5-minute lows today."""

    def __init__(self):
        self.btc_low = 99.0   # BTC yesterday close is 100 -> 1% drop

    def __call__(self, symbol, interval, start_ms=None, limit=1000):
        if interval == "1d":
            return [[DAY - DAY_MS, "100", "100", "100", "100", "0", DAY - 1],
                    [DAY, "100", "100", "100", "100", "0", DAY + DAY_MS - 1]]
        return [[DAY + 300_000, "100", "100", str(self.btc_low), "100", "0", DAY + 599_999]]


class LiveDipCatcherTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "manifest.json").write_text(json.dumps({"symbols": ["BTCUSDT", "SOLUSDT", "ETHUSDT", "XRPUSDT"]}))
        self.ex, self.sent, self.now = FakeExecutor(), [], [DAY + 60_000]
        self.prices = {"SOLUSDT": 95, "ETHUSDT": 95}
        self.kl = Klines()

    def _dip(self, config=CONFIG):
        return LiveDipCatcher(config, self.dir / "dip.json", self.dir / "manifest.json", self.ex, FakeMarket(),
                              send=self.sent.append, clock=lambda: self.now[0] / 1000, equity_fn=lambda: 200,
                              klines=self.kl, price_fn=lambda s: self.prices[s])

    def _id(self, prefix, symbol):
        return LiveDipCatcher._id(prefix, symbol, DAY)

    def test_watches_the_top_coins_at_ten_percent_under_and_rests_nothing(self):
        state = self._dip().tick()
        self.assertEqual(state["watch"], {"SOLUSDT": "90.0", "ETHUSDT": "90.0"})
        self.assertEqual([c for c in self.ex.calls if c[0] == "buy"], [])
        self.assertEqual(len(self.sent), 1)
        self.assertIn("BTC", self.sent[0])

    def test_buys_at_market_when_a_coin_reaches_its_level_and_btc_is_calm(self):
        dip = self._dip()
        dip.tick()
        self.prices["SOLUSDT"] = 89.5
        state = dip.tick()
        buy = next(c for c in self.ex.calls if c[0] == "buy")
        self.assertEqual((buy[1], buy[2]), ("SOLUSDT", "0.223"))       # 20 USDT / 89.5, rounded down
        self.assertTrue(buy[3].startswith("kv1fc"))
        stop = next(c for c in self.ex.calls if c[0] == "stop")
        tp = next(c for c in self.ex.calls if c[0] == "tp")
        self.assertEqual((Decimal(stop[3]), Decimal(tp[3])), (Decimal("81"), Decimal("92.70")))  # -10% / +3% of 90
        self.assertIn(("leverage", "SOLUSDT", 3), self.ex.calls)
        self.assertIn("SOLUSDT", state["positions"])

    def test_no_buy_while_btc_has_fallen_more_than_the_calm_limit(self):
        dip = self._dip()
        dip.tick()
        self.kl.btc_low = 97.5                                          # BTC -2.5% today
        self.prices["SOLUSDT"] = 89.0
        dip.tick()
        dip.tick()
        self.assertEqual([c for c in self.ex.calls if c[0] == "buy"], [])
        self.assertEqual(sum("BTC de bugun sert dustu" in m for m in self.sent), 1)   # told once

    def test_once_per_coin_per_day(self):
        dip = self._dip()
        dip.tick()
        self.prices["SOLUSDT"] = 89.0
        dip.tick()
        self.ex.amounts["SOLUSDT"] = Decimal("0")          # e.g. the take-profit fired
        self.ex.open.pop(self._id("kv1fg", "SOLUSDT"), None)
        dip.tick()
        dip.tick()
        self.assertEqual(len([c for c in self.ex.calls if c[0] == "buy"]), 1)

    def test_skips_a_coin_held_or_busy(self):
        self.ex.amounts["SOLUSDT"] = Decimal("1")
        self.ex.open["kv1fq99991790640000000"] = "ETHUSDT"   # a trend stop rests on ETH
        dip = self._dip()
        dip.tick()
        self.prices.update(SOLUSDT=80, ETHUSDT=80)
        dip.tick()
        self.assertEqual([c for c in self.ex.calls if c[0] == "buy"], [])

    def test_take_profit_fired_cancels_the_stop(self):
        dip = self._dip()
        dip.tick()
        self.prices["SOLUSDT"] = 89.0
        dip.tick()
        self.ex.amounts["SOLUSDT"] = Decimal("0")
        self.ex.open.pop(self._id("kv1fg", "SOLUSDT"))
        state = dip.tick()
        self.assertIn(("cancel", "SOLUSDT", self._id("kv1fz", "SOLUSDT")), self.ex.calls)
        self.assertEqual(state["closed"][0]["reason"], "hedef")

    def test_time_exit_after_24_hours(self):
        dip = self._dip()
        dip.tick()
        self.prices["SOLUSDT"] = 89.0
        dip.tick()
        self.now[0] += 24 * 3_600_000 + 1
        self.prices["SOLUSDT"] = 95
        state = dip.tick()
        self.assertIn(("close", "SOLUSDT", "0.224", self._id("kv1fw", "SOLUSDT")), self.ex.calls)
        self.assertEqual(state["closed"][0]["reason"], "sure doldu")

    def test_a_stop_that_cannot_be_placed_closes_at_market(self):
        self.ex.fail_stop = True
        dip = self._dip()
        dip.tick()
        self.prices["SOLUSDT"] = 89.0
        state = dip.tick()
        self.assertTrue(any(c[0] == "close" for c in self.ex.calls))
        self.assertIn("acil kapatma", state["closed"][0]["reason"])

    def test_version_one_resting_buys_are_cancelled_and_a_filled_one_protected(self):
        v1 = {"day": DAY, "orders": {"XRPUSDT": {"id": "kv1fdAAAA", "price": "1.20", "qty": "10"},
                                     "DOGEUSDT": {"id": "kv1fdBBBB", "price": "0.08", "qty": "100"}},
              "positions": {}, "closed": []}
        (self.dir / "dip.json").write_text(json.dumps(v1))
        self.ex.orders["kv1fdAAAA"] = {"status": "NEW"}
        self.ex.open["kv1fdAAAA"] = "XRPUSDT"
        self.ex.orders["kv1fdBBBB"] = {"status": "FILLED"}              # fired before the switch
        self.ex.amounts["DOGEUSDT"] = Decimal("100")
        self.ex.entry["DOGEUSDT"] = Decimal("0.08")
        state = self._dip().tick()
        self.assertIn(("cancel", "XRPUSDT", "kv1fdAAAA"), self.ex.calls)
        self.assertEqual(state["orders"], {})
        self.assertIn("DOGEUSDT", state["positions"])
        self.assertTrue(any(c[0] == "stop" and c[1] == "DOGEUSDT" for c in self.ex.calls))

    def test_off_unless_mode_is_live(self):
        for config in (None, {}, dict(CONFIG, mode="paper")):
            self.assertIsNone(self._dip(config).tick())
        self.assertEqual(self.ex.calls, [])


if __name__ == "__main__":
    unittest.main()
