import json
import unittest
from pathlib import Path
from crypto_v1.data import INTERVAL
from crypto_v1.friend_v1 import sma, rsi14, tr_trading_hours, evaluate, SYMBOLS

C = json.loads((Path(__file__).parents[1] / 'config.json').read_text())


def flat_rows(n, t0=0):
    return [dict(t=t0 + i * INTERVAL, o=100.0, h=100.5, l=99.5, c=100.0, v=100.0) for i in range(n)]


class FriendV1Tests(unittest.TestCase):
    def test_sma_needs_full_window(self):
        self.assertIsNone(sma([1, 2], 3))
        self.assertAlmostEqual(sma([1, 2, 3], 3), 2)

    def test_rsi_all_gains_is_100(self):
        closes = [100 + i for i in range(20)]
        self.assertEqual(rsi14(closes), 100.0)

    def test_tr_trading_hours_boundary(self):
        # 06:00 UTC = 09:00 Turkey -> open; 20:00 UTC = 23:00 Turkey -> closed
        self.assertTrue(tr_trading_hours(6 * 3600 * 1000))
        self.assertFalse(tr_trading_hours(20 * 3600 * 1000))
        self.assertFalse(tr_trading_hours(0))

    def test_evaluate_no_signal_data_is_no_go(self):
        n = 1200
        data = {s: flat_rows(n) for s in SYMBOLS}
        manifest = dict(start=0, end=n * INTERVAL, symbols=SYMBOLS)
        result = evaluate(data, C, manifest, count=2, min_trades=1)
        self.assertEqual(result['verdict'], 'NO_GO')
        self.assertEqual(len(result['windows']), 2)
        for w in result['windows']:
            self.assertEqual(w['normal']['trade_count'], 0)


if __name__ == '__main__':
    unittest.main()
