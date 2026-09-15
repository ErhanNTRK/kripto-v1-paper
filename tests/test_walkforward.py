import json
import unittest
from pathlib import Path
from crypto_v1.data import INTERVAL
from crypto_v1.walkforward import stress_config, windows_from_times, evaluate, report

C = json.loads((Path(__file__).parents[1] / 'config.json').read_text())


def flat_rows(n, t0=0):
    return [dict(t=t0 + i * INTERVAL, o=100.0, h=100.5, l=99.5, c=100.0, v=100.0) for i in range(n)]


class WalkforwardTests(unittest.TestCase):
    def test_stress_config_doubles_costs_within_bounds(self):
        cs = stress_config(C)
        self.assertAlmostEqual(cs['fee'], C['fee'] * 2)
        self.assertAlmostEqual(cs['slippage'], C['slippage'] * 2)
        self.assertLess(cs['fee'], 0.02)

    def test_windows_from_times_splits_evenly(self):
        times = list(range(1000))
        starts = windows_from_times(times, 4)
        self.assertEqual(len(starts), 4)
        self.assertEqual(starts, sorted(starts))
        self.assertGreaterEqual(starts[0], times[200])

    def test_windows_from_times_rejects_too_few_windows_or_data(self):
        with self.assertRaises(ValueError):
            windows_from_times(list(range(1000)), 1)
        with self.assertRaises(ValueError):
            windows_from_times(list(range(50)), 4)

    def test_evaluate_no_signal_data_is_no_go_with_reasons(self):
        n = 1200
        data = {'BTCUSDT': flat_rows(n)}
        manifest = dict(start=0, end=n * INTERVAL, symbols=[])
        result = evaluate(data, [], C, manifest, count=3)
        self.assertEqual(result['verdict'], 'NO_GO')
        self.assertEqual(len(result['windows']), 3)
        self.assertTrue(result['reasons'])
        for w in result['windows']:
            self.assertEqual(w['normal']['trade_count'], 0)
            self.assertFalse(w['passed'])

    def test_evaluate_requires_min_candles(self):
        data = {'BTCUSDT': flat_rows(100)}
        manifest = dict(start=0, end=100 * INTERVAL, symbols=[])
        with self.assertRaises(ValueError):
            evaluate(data, [], C, manifest, count=2)

    def test_report_writes_summary_files(self):
        n = 1200
        data = {'BTCUSDT': flat_rows(n)}
        manifest = dict(start=0, end=n * INTERVAL, symbols=[])
        result = evaluate(data, [], C, manifest, count=2)
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'wf'
            full = report(result, dict(config_name='config.json'), out)
            self.assertTrue((out / 'summary.json').exists())
            self.assertTrue((out / 'SUMMARY.md').exists())
            self.assertEqual(full['verdict'], 'NO_GO')


if __name__ == '__main__':
    unittest.main()
