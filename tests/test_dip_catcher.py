import json
import tempfile
import unittest
from pathlib import Path

from crypto_v1.dip_catcher import COST, DAY_MS, DipCatcher

DAY = 1_790_640_000_000 // DAY_MS * DAY_MS   # a UTC day start
M5 = 300_000
CONFIG = {"mode": "paper", "top_n": 2, "depth": 0.15, "take_profit": 0.08, "stop": 0.25,
          "hold_hours": 24, "size_fraction": 0.10}


def candle(open_ms, o, h, l, c, length=M5):
    return [open_ms, str(o), str(h), str(l), str(c), "0", open_ms + length - 1]


class FakeMarket:
    """Daily closes 100 (the test day's yesterday) and 90 (the test day itself); 5-minute candles per coin."""

    def __init__(self):
        self.five = {}
        self.fail = set()

    def __call__(self, symbol, interval, start_ms=None, limit=1000):
        if symbol in self.fail:
            raise OSError("down")
        if interval == "1d":
            return [candle(DAY - DAY_MS, 100, 100, 100, 100, DAY_MS), candle(DAY, 90, 90, 90, 90, DAY_MS)]
        return [r for r in self.five.get(symbol, []) if r[0] >= start_ms]


class DipCatcherTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "manifest.json").write_text(json.dumps({"symbols": ["BTCUSDT", "SOLUSDT", "ETHUSDT", "XRPUSDT"]}))
        self.market, self.sent, self.now = FakeMarket(), [], [DAY + 60_000]

    def _dip(self, config=CONFIG, held=()):
        return DipCatcher(config, self.dir / "dip.json", self.dir / "manifest.json", send=self.sent.append,
                          klines=self.market, clock=lambda: self.now[0] / 1000, equity_fn=lambda: 200,
                          held_fn=lambda: set(held))

    def test_places_orders_on_the_top_coins_skipping_btc_and_held_ones(self):
        state = self._dip(held=("SOLUSDT",)).tick()
        self.assertEqual(sorted(state["orders"]), ["ETHUSDT"])          # top 2 minus BTC minus held SOL
        self.assertAlmostEqual(state["orders"]["ETHUSDT"]["limit"], 85.0)
        self.assertEqual(state["orders"]["ETHUSDT"]["size_usdt"], 20.0)   # 10% of 200
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Gercek emir yok", self.sent[0])

    def test_fills_on_a_closed_candle_only_and_never_takes_profit_on_the_fill_candle(self):
        dip = self._dip()
        dip.tick()
        self.market.five["SOLUSDT"] = [candle(DAY + M5, 90, 99, 84, 95),       # touches 85: fill; high 99 >= tp ignored
                                       candle(DAY + 2 * M5, 95, 96, 94, 95)]   # still open when now is inside it
        self.now[0] = DAY + 2 * M5 + 1000
        state = dip.tick()
        pos = state["positions"]["SOLUSDT"]
        self.assertAlmostEqual(pos["entry"], 85 * (1 + COST))
        self.assertNotIn("SOLUSDT", state["orders"])
        self.assertEqual(state["closed"], [])
        self.assertTrue(any("DOLDU" in m for m in self.sent))

    def test_take_profit_then_stop_then_time_exit(self):
        dip = self._dip()
        dip.tick()
        tp = 85 * (1 + COST) * 1.08
        self.market.five["SOLUSDT"] = [candle(DAY + M5, 90, 90, 84, 86), candle(DAY + 2 * M5, 86, tp + 1, 86, 90)]
        self.market.five["ETHUSDT"] = [candle(DAY + M5, 90, 90, 84, 86), candle(DAY + 2 * M5, 86, 87, 60, 61)]
        self.now[0] = DAY + 3 * M5 + 1000
        state = dip.tick()
        reasons = {r["symbol"]: r["reason"] for r in state["closed"]}
        self.assertEqual(reasons, {"SOLUSDT": "hedef", "ETHUSDT": "stop"})
        sol = next(r for r in state["closed"] if r["symbol"] == "SOLUSDT")
        self.assertAlmostEqual(sol["exit"], tp * (1 - COST))
        self.assertGreater(sol["pnl"], 0)
        eth = next(r for r in state["closed"] if r["symbol"] == "ETHUSDT")
        self.assertAlmostEqual(eth["pct"], (85 * (1 + COST) * 0.75 * (1 - COST)) / (85 * (1 + COST)) - 1)

    def test_time_exit_after_hold_hours(self):
        dip = self._dip()
        dip.tick()
        rows = [candle(DAY + M5, 90, 90, 84, 86)]
        rows += [candle(DAY + (2 + i) * M5, 86, 87, 85.5, 86.5) for i in range(24 * 12 + 2)]
        self.market.five["SOLUSDT"] = rows
        self.now[0] = DAY + (2 + 24 * 12 + 2) * M5 + 1000
        state = dip.tick()
        closed = [r for r in state["closed"] if r["symbol"] == "SOLUSDT"]
        self.assertEqual(closed[0]["reason"], "sure doldu")

    def test_orders_expire_at_the_day_end_but_positions_carry_over(self):
        dip = self._dip()
        dip.tick()
        self.market.five["SOLUSDT"] = [candle(DAY + M5, 90, 90, 84, 86)]
        # a candle of the NEXT day under yesterday's ETH limit (85) must not fill yesterday's order;
        # today's order sits lower (90 x 0.85 = 76.5) and is not reached either
        self.market.five["ETHUSDT"] = [candle(DAY + DAY_MS + M5, 82, 82, 80, 81)]
        self.now[0] = DAY + DAY_MS + 3 * M5
        state = dip.tick()
        self.assertEqual(state["day"], DAY + DAY_MS)
        self.assertIn("SOLUSDT", state["positions"])                  # filled yesterday, still open
        self.assertNotIn("ETHUSDT", state["positions"])
        self.assertIn("ETHUSDT", state["orders"])                     # today's fresh order instead
        self.assertAlmostEqual(state["orders"]["ETHUSDT"]["limit"], 76.5)

    def test_one_coins_data_error_does_not_stop_the_others(self):
        dip = self._dip()
        dip.tick()
        self.market.fail.add("ETHUSDT")
        self.market.five["SOLUSDT"] = [candle(DAY + M5, 90, 90, 84, 86)]
        self.now[0] = DAY + 2 * M5
        state = dip.tick()
        self.assertIn("SOLUSDT", state["positions"])
        self.assertIn("ETHUSDT", state["orders"])

    def test_off_unless_mode_is_paper(self):
        for config in (None, {}, dict(CONFIG, mode="off"), dict(CONFIG, mode="live")):
            self.assertIsNone(self._dip(config).tick())
        self.assertFalse((self.dir / "dip.json").exists())

    def test_state_survives_a_restart(self):
        self._dip().tick()
        self.market.five["SOLUSDT"] = [candle(DAY + M5, 90, 90, 84, 86)]
        self.now[0] = DAY + 2 * M5
        self._dip().tick()                       # a new object, as after a restart
        state = self._dip().tick()
        self.assertIn("SOLUSDT", state["positions"])
        self.assertEqual(sum("DOLDU" in m for m in self.sent), 1)   # not announced twice
        self.assertEqual(sum("bekleyen alim" in m for m in self.sent), 1)


if __name__ == "__main__":
    unittest.main()
