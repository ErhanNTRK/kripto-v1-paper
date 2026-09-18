import unittest

from crypto_v1.data import INTERVAL
from crypto_v1.short_signal import (LEVERAGE_NORMAL, LEVERAGE_STRONG,
                                     detect_long_candidates, detect_short_candidates,
                                     leverage_for_signal)

C = dict(atr_multiplier=2.0, risk_fraction=0.005, max_positions=3,
         fee=0.001, slippage=0.0005, initial_cash=10000.0,
         breakout_bars=20, support_bars=10)


class LeverageForSignalTests(unittest.TestCase):
    def test_two_of_three_windows_breaking_is_normal_leverage(self):
        self.assertEqual(leverage_for_signal(2), LEVERAGE_NORMAL)

    def test_all_three_windows_breaking_is_strong_leverage(self):
        self.assertEqual(leverage_for_signal(3), LEVERAGE_STRONG)


def _trending_rows(n, step, offset=0.5):
    out = []
    price = 1000.0
    for i in range(n):
        price += step
        out.append(dict(t=i * INTERVAL, o=price, h=price + offset, l=price - offset, c=price, v=100.0))
    return out


class DetectShortCandidatesTests(unittest.TestCase):
    def test_no_btc_data_returns_nothing(self):
        self.assertEqual(detect_short_candidates({}, ['ALTUSDT'], C), [])

    def test_flat_data_produces_no_candidates(self):
        n = 260
        rows = [dict(t=i * INTERVAL, o=100.0, h=100.5, l=99.5, c=100.0, v=100.0) for i in range(n)]
        data = {'BTCUSDT': rows, 'ALTUSDT': rows}
        self.assertEqual(detect_short_candidates(data, ['ALTUSDT'], C), [])

    def test_downtrend_with_btc_confirmation_yields_a_candidate(self):
        n = 260
        data = {'BTCUSDT': _trending_rows(n, -2.0), 'ALTUSDT': _trending_rows(n, -2.0)}
        candidates = detect_short_candidates(data, ['ALTUSDT'], C)
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate['symbol'], 'ALTUSDT')
        self.assertGreater(candidate['stop'], candidate['close'])
        self.assertIn(candidate['leverage'], (LEVERAGE_NORMAL, LEVERAGE_STRONG))

    def test_uptrend_does_not_yield_a_short_candidate(self):
        n = 260
        data = {'BTCUSDT': _trending_rows(n, 2.0), 'ALTUSDT': _trending_rows(n, 2.0)}
        self.assertEqual(detect_short_candidates(data, ['ALTUSDT'], C), [])

    def test_short_history_is_skipped_not_crashed(self):
        data = {'BTCUSDT': _trending_rows(260, -2.0), 'ALTUSDT': _trending_rows(50, -2.0)}
        self.assertEqual(detect_short_candidates(data, ['ALTUSDT'], C), [])


class DetectLongCandidatesTests(unittest.TestCase):
    """Mirrors DetectShortCandidatesTests -- this is the new lightweight
    scan added for the 2H system (18 Sep 2026); the 4H long side still
    detects via paper_trading.tick()'s Engine, unchanged."""

    def test_no_btc_data_returns_nothing(self):
        self.assertEqual(detect_long_candidates({}, ['ALTUSDT'], C), [])

    def test_uptrend_with_btc_confirmation_yields_a_candidate(self):
        n = 260
        data = {'BTCUSDT': _trending_rows(n, 2.0), 'ALTUSDT': _trending_rows(n, 2.0)}
        candidates = detect_long_candidates(data, ['ALTUSDT'], C)
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate['symbol'], 'ALTUSDT')
        self.assertLess(candidate['stop'], candidate['close'])
        self.assertIn(candidate['leverage'], (LEVERAGE_NORMAL, LEVERAGE_STRONG))

    def test_downtrend_does_not_yield_a_long_candidate(self):
        n = 260
        data = {'BTCUSDT': _trending_rows(n, -2.0), 'ALTUSDT': _trending_rows(n, -2.0)}
        self.assertEqual(detect_long_candidates(data, ['ALTUSDT'], C), [])


if __name__ == '__main__':
    unittest.main()
