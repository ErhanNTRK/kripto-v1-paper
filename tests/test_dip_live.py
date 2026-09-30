import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from crypto_v1.binance_trade import OrderRejected, OrderStateUnknown
from crypto_v1.dip_catcher import DAY_MS
from crypto_v1.dip_live import LiveDipCatcher

DAY = 1_790_640_000_000 // DAY_MS * DAY_MS
CONFIG = {"mode": "live", "top_n": 2, "depth": 0.15, "take_profit": 0.08, "stop": 0.25,
          "hold_hours": 24, "size_fraction": 0.10}
RULES = {"tick_size": Decimal("0.01"), "step_size": Decimal("0.001"), "min_qty": Decimal("0.001"),
         "min_notional": Decimal("5")}


class FakeExecutor:
    def __init__(self):
        self.amounts, self.entry, self.orders, self.open, self.calls = {}, {}, {}, set(), []
        self.fail_stop = False
        self.unknown_on_place = False
        self.close_price = {}

    def account(self):
        return {"positions": [{"symbol": s, "positionAmt": str(a), "entryPrice": str(self.entry.get(s, "0"))}
                              for s, a in self.amounts.items()]}

    def open_orders(self):
        return [{"clientOrderId": cid} for cid in self.open]

    def query(self, symbol, cid):
        if cid not in self.orders:
            raise OrderRejected(-2013)
        return self.orders[cid]

    def cancel(self, symbol, cid):
        self.calls.append(("cancel", symbol, cid))
        self.open.discard(cid)
        if cid in self.orders and self.orders[cid]["status"] == "NEW":
            self.orders[cid]["status"] = "CANCELED"
        return {"status": "CANCELED"}

    def set_isolated_margin(self, symbol):
        self.calls.append(("isolated", symbol))

    def set_leverage(self, symbol, leverage):
        self.calls.append(("leverage", symbol, leverage))

    def triggered_open_long(self, symbol, qty, price, cid):
        self.calls.append(("limit", symbol, qty, price, cid))
        self.orders[cid] = {"status": "NEW", "executedQty": "0", "avgPrice": "0", "updateTime": DAY}
        self.open.add(cid)
        if self.unknown_on_place:
            raise OrderStateUnknown("timeout")

    def protective_stop_for_long(self, symbol, qty, stop, cid):
        self.calls.append(("stop", symbol, qty, stop, cid))
        if self.fail_stop:
            raise OrderRejected(-2021)
        self.open.add(cid)

    def take_profit_for_long(self, symbol, qty, tp, cid):
        self.calls.append(("tp", symbol, qty, tp, cid))
        self.open.add(cid)

    def market_close_long(self, symbol, qty, cid):
        self.calls.append(("close", symbol, qty, cid))
        self.amounts[symbol] = Decimal("0")
        return {"avgPrice": str(self.close_price.get(symbol, "90"))}

    # test helpers
    def fill(self, cid, symbol, qty, price):
        self.orders[cid].update(status="FILLED", updateTime=DAY + 3_600_000)   # a fired conditional buy
        self.open.discard(cid)
        self.amounts[symbol] = Decimal(str(qty))
        self.entry[symbol] = Decimal(str(price))


class FakeMarket:
    def rules(self, symbol):
        return RULES


def klines(symbol, interval, start_ms=None, limit=1000):
    # yesterday's daily close 100 for every coin
    return [[DAY - DAY_MS, "100", "100", "100", "100", "0", DAY - 1], [DAY, "100", "100", "100", "100", "0", DAY + DAY_MS - 1]]


class LiveDipCatcherTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "manifest.json").write_text(json.dumps({"symbols": ["BTCUSDT", "SOLUSDT", "ETHUSDT", "XRPUSDT"]}))
        self.ex, self.sent, self.now = FakeExecutor(), [], [DAY + 60_000]

    def _dip(self, config=CONFIG):
        return LiveDipCatcher(config, self.dir / "dip.json", self.dir / "manifest.json", self.ex, FakeMarket(),
                              send=self.sent.append, clock=lambda: self.now[0] / 1000, equity_fn=lambda: 200,
                              klines=klines)

    def _id(self, prefix, symbol):
        return LiveDipCatcher._id(prefix, symbol, DAY)

    def test_places_triggered_buys_on_the_top_coins_at_3x_isolated(self):
        state = self._dip().tick()
        limits = [c for c in self.ex.calls if c[0] == "limit"]
        self.assertEqual([c[1] for c in limits], ["SOLUSDT", "ETHUSDT"])
        self.assertEqual(limits[0][3], "85.00")                   # 100 x 0.85
        self.assertEqual(limits[0][2], "0.235")                   # 20 USDT / 85, rounded down to the step
        self.assertTrue(limits[0][4].startswith("kv1fd"))
        self.assertIn(("leverage", "SOLUSDT", 3), self.ex.calls)
        self.assertIn(("isolated", "SOLUSDT"), self.ex.calls)
        self.assertEqual(sorted(state["orders"]), ["ETHUSDT", "SOLUSDT"])
        self.assertEqual(len(self.sent), 1)

    def test_skips_a_coin_already_held_and_places_once_per_day(self):
        self.ex.amounts["SOLUSDT"] = Decimal("1")               # a trend position
        dip = self._dip()
        dip.tick()
        dip.tick()
        self.assertEqual([c[1] for c in self.ex.calls if c[0] == "limit"], ["ETHUSDT"])

    def test_a_fill_gets_its_stop_and_take_profit(self):
        dip = self._dip()
        dip.tick()
        self.ex.fill(self._id("kv1fd", "SOLUSDT"), "SOLUSDT", "0.235", "85")
        self.now[0] += 120_000
        state = dip.tick()
        stop = next(c for c in self.ex.calls if c[0] == "stop")
        tp = next(c for c in self.ex.calls if c[0] == "tp")
        self.assertEqual((stop[2], stop[3]), ("0.235", "63.75"))   # -25%
        self.assertEqual(tp[3], "91.80")                           # +8%
        self.assertIn("SOLUSDT", state["positions"])
        self.assertNotIn("SOLUSDT", state["orders"])
        self.assertTrue(any("ALDIM" in m for m in self.sent))

    def test_take_profit_fired_cancels_the_stop_and_records_it(self):
        dip = self._dip()
        dip.tick()
        self.ex.fill(self._id("kv1fd", "SOLUSDT"), "SOLUSDT", "0.235", "85")
        dip.tick()
        # the take-profit fired: position gone, its order gone, the stop still resting
        self.ex.amounts["SOLUSDT"] = Decimal("0")
        self.ex.open.discard(self._id("kv1fg", "SOLUSDT"))
        state = dip.tick()
        self.assertIn(("cancel", "SOLUSDT", self._id("kv1fz", "SOLUSDT")), self.ex.calls)
        self.assertEqual(state["closed"][0]["reason"], "hedef")
        self.assertAlmostEqual(state["closed"][0]["exit"], 91.80)

    def test_stop_fired_is_recorded_as_stop(self):
        dip = self._dip()
        dip.tick()
        self.ex.fill(self._id("kv1fd", "SOLUSDT"), "SOLUSDT", "0.235", "85")
        dip.tick()
        self.ex.amounts["SOLUSDT"] = Decimal("0")
        self.ex.open.discard(self._id("kv1fz", "SOLUSDT"))
        state = dip.tick()
        self.assertIn(("cancel", "SOLUSDT", self._id("kv1fg", "SOLUSDT")), self.ex.calls)
        self.assertEqual(state["closed"][0]["reason"], "stop")

    def test_time_exit_cancels_both_and_sells_at_market(self):
        dip = self._dip()
        dip.tick()
        self.ex.fill(self._id("kv1fd", "SOLUSDT"), "SOLUSDT", "0.235", "85")
        dip.tick()
        self.ex.close_price["SOLUSDT"] = "88"
        self.now[0] = DAY + 3_600_000 + 24 * 3_600_000 + 1
        state = dip.tick()
        self.assertIn(("close", "SOLUSDT", "0.235", self._id("kv1fw", "SOLUSDT")), self.ex.calls)
        self.assertIn(("cancel", "SOLUSDT", self._id("kv1fg", "SOLUSDT")), self.ex.calls)
        self.assertIn(("cancel", "SOLUSDT", self._id("kv1fz", "SOLUSDT")), self.ex.calls)
        self.assertEqual(state["closed"][0]["reason"], "sure doldu")
        self.assertAlmostEqual(state["closed"][0]["exit"], 88.0)

    def test_a_stop_that_cannot_be_placed_closes_at_market(self):
        self.ex.fail_stop = True
        dip = self._dip()
        dip.tick()
        self.ex.fill(self._id("kv1fd", "SOLUSDT"), "SOLUSDT", "0.235", "85")
        state = dip.tick()
        self.assertTrue(any(c[0] == "close" and c[1] == "SOLUSDT" for c in self.ex.calls))
        self.assertIn("acil kapatma", state["closed"][0]["reason"])
        self.assertNotIn("SOLUSDT", state["positions"])

    def test_a_trend_buy_on_the_same_coin_cancels_the_resting_order(self):
        dip = self._dip()
        dip.tick()
        self.ex.amounts["SOLUSDT"] = Decimal("2")                # a trend system bought SOL
        state = dip.tick()
        self.assertIn(("cancel", "SOLUSDT", self._id("kv1fd", "SOLUSDT")), self.ex.calls)
        self.assertNotIn("SOLUSDT", state["orders"])
        self.assertNotIn("SOLUSDT", state["positions"])

    def test_the_next_day_cancels_yesterdays_orders_and_places_new_ones(self):
        dip = self._dip()
        dip.tick()
        self.now[0] = DAY + DAY_MS + 60_000
        dip.tick()
        cancels = [c for c in self.ex.calls if c[0] == "cancel"]
        self.assertEqual({c[2] for c in cancels}, {self._id("kv1fd", "SOLUSDT"), self._id("kv1fd", "ETHUSDT")})
        new_ids = [c[4] for c in self.ex.calls if c[0] == "limit"][2:]
        self.assertEqual(new_ids, [LiveDipCatcher._id("kv1fd", s, DAY + DAY_MS) for s in ("SOLUSDT", "ETHUSDT")])

    def test_an_unknown_placement_is_settled_by_query_not_resent(self):
        self.ex.unknown_on_place = True
        dip = self._dip()
        state = dip.tick()
        self.assertEqual(sorted(state["orders"]), ["ETHUSDT", "SOLUSDT"])
        state = dip.tick()
        self.assertEqual(len([c for c in self.ex.calls if c[0] == "limit"]), 2)
        self.assertEqual(sorted(state["orders"]), ["ETHUSDT", "SOLUSDT"])

    def test_off_unless_mode_is_live(self):
        for config in (None, {}, dict(CONFIG, mode="paper")):
            self.assertIsNone(self._dip(config).tick())
        self.assertEqual(self.ex.calls, [])


if __name__ == "__main__":
    unittest.main()
