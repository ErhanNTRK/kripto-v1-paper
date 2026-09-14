import unittest
from crypto_v1.research_v2 import DonchianModel, hourly


class ResearchV2Tests(unittest.TestCase):
    def test_hourly_uses_only_complete_four_bar_groups(self):
        rows = [{"t": i*900000, "o": i+1, "h": i+2, "l": i+.5, "c": i+1.5, "v": 1}
                for i in range(5)]
        self.assertEqual(hourly(rows), [{"t": 0, "o": 1, "h": 5, "l": .5, "c": 4.5, "v": 4}])

    def test_donchian_requires_two_breakouts_and_btc_filter(self):
        row = {"c": 110, "atr": 2, "support": 100, "donchian_breaks": 2}
        btc = {"c": 110, "ema20": 105, "ema50": 100, "ema200": 90}
        c = {"atr_multiplier": 2}
        self.assertTrue(DonchianModel.buy(row, btc, c))
        self.assertFalse(DonchianModel.buy(dict(row, donchian_breaks=1), btc, c))
