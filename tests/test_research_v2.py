import json
import unittest
from pathlib import Path
from crypto_v1.research_v2 import DonchianModel, hourly, aggregate, evaluate_walkforward, \
    evaluate_timeframe_walkforward, FOUR_HOUR
from crypto_v1.data import INTERVAL

C = json.loads((Path(__file__).parents[1] / 'config_v2.json').read_text())


class ResearchV2Tests(unittest.TestCase):
    def test_hourly_uses_only_complete_four_bar_groups(self):
        rows = [{"t": i*900000, "o": i+1, "h": i+2, "l": i+.5, "c": i+1.5, "v": 1}
                for i in range(5)]
        self.assertEqual(hourly(rows), [{"t": 0, "o": 1, "h": 5, "l": .5, "c": 4.5, "v": 4}])

    def test_aggregate_uses_only_complete_groups_for_any_interval(self):
        rows = [{"t": i*900000, "o": i+1, "h": i+2, "l": i+.5, "c": i+1.5, "v": 1}
                for i in range(16)]
        four_hour = aggregate(rows, FOUR_HOUR)
        self.assertEqual(len(four_hour), 1)
        self.assertEqual(four_hour[0], {"t": 0, "o": 1, "h": 17, "l": .5, "c": 16.5, "v": 16})

    def test_donchian_requires_two_breakouts_and_btc_filter(self):
        row = {"c": 110, "atr": 2, "support": 100, "donchian_breaks": 2}
        btc = {"c": 110, "ema20": 105, "ema50": 100, "ema200": 90}
        c = {"atr_multiplier": 2}
        self.assertTrue(DonchianModel.buy(row, btc, c))
        self.assertFalse(DonchianModel.buy(dict(row, donchian_breaks=1), btc, c))

    def test_evaluate_walkforward_runs_without_crashing_on_flat_data(self):
        n = 1700
        rows = [dict(t=i * INTERVAL, o=100.0, h=100.5, l=99.5, c=100.0, v=100.0) for i in range(n)]
        data = {'BTCUSDT': rows}
        manifest = dict(start=0, end=n * INTERVAL, symbols=['BTCUSDT'])
        result = evaluate_walkforward(data, ['BTCUSDT'], C, manifest, count=2, min_trades=1)
        self.assertIn(result['verdict'], ('GO', 'NO_GO'))
        self.assertEqual(len(result['windows']), 2)
        # Flat price data has no Donchian breakout, so no trades and a NO_GO verdict.
        self.assertEqual(result['verdict'], 'NO_GO')

    def test_evaluate_timeframe_walkforward_runs_without_crashing_on_flat_data(self):
        n = 1700
        rows = [dict(t=i * INTERVAL, o=100.0, h=100.5, l=99.5, c=100.0, v=100.0) for i in range(n)]
        data = {'BTCUSDT': rows}
        manifest = dict(start=0, end=n * INTERVAL, symbols=['BTCUSDT'])
        result = evaluate_timeframe_walkforward(data, ['BTCUSDT'], C, manifest, FOUR_HOUR,
                                                  count=2, min_trades=1, warm=10)
        self.assertEqual(result['verdict'], 'NO_GO')
        self.assertEqual(len(result['windows']), 2)
