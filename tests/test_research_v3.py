import unittest

from crypto_v1.research_v3 import CostAwareTrend, six_hour


class ResearchV3Tests(unittest.TestCase):
    def test_six_hour_requires_24_complete_bars(self):
        rows = [{"t": i*900000, "o": i+1, "h": i+2, "l": i+.5, "c": i+1.5, "v": 1}
                for i in range(25)]
        self.assertEqual(len(six_hour(rows)), 1)
        self.assertEqual(six_hour(rows)[0]["v"], 24)

    def test_buy_requires_trend_breakout_and_cost_hurdle(self):
        row = {"c": 110, "ema20": 108, "ema50": 105, "ema200": 100,
               "atr": 2, "support": 102, "break20": True}
        btc = {"c": 110, "ema50": 105, "ema200": 100}
        self.assertTrue(CostAwareTrend.buy(row, btc, {"atr_multiplier": 2}))
        self.assertFalse(CostAwareTrend.buy(dict(row, break20=False), btc, {"atr_multiplier": 2}))
