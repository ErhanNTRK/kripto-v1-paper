import unittest
from crypto_v1.research_v5 import (
    btc_up_ok, btc_down_ok, long_entry, short_entry, long_exit, short_exit,
    long_stop, ShortWindowLongModel, run_symmetric, evaluate,
)
from crypto_v1.data import INTERVAL

C = dict(atr_multiplier=2.0, risk_fraction=0.005, max_positions=3,
         fee=0.001, slippage=0.0005, initial_cash=10000.0,
         breakout_bars=20, support_bars=10)


def row(**kw):
    base = dict(c=100.0, atr=1.0, breaks_up=2, breaks_down=0, long_exit=95.0, short_exit=105.0)
    base.update(kw)
    return base


def up_btc():
    return dict(c=100, ema20=98, ema50=96, ema200=90)


def down_btc():
    return dict(c=100, ema20=102, ema50=104, ema200=110)


class DirectionFiltersTests(unittest.TestCase):
    def test_btc_up_ok_requires_stacked_upward_emas(self):
        self.assertTrue(btc_up_ok(up_btc()))
        self.assertFalse(btc_up_ok(down_btc()))

    def test_btc_down_ok_requires_stacked_downward_emas(self):
        self.assertTrue(btc_down_ok(down_btc()))
        self.assertFalse(btc_down_ok(up_btc()))


class EntryExitTests(unittest.TestCase):
    def test_long_entry_needs_btc_up_and_two_breaks(self):
        self.assertIsNotNone(long_entry(row(breaks_up=2), up_btc(), C))
        self.assertIsNone(long_entry(row(breaks_up=1), up_btc(), C))
        self.assertIsNone(long_entry(row(breaks_up=2), down_btc(), C))

    def test_short_entry_needs_btc_down_and_two_breaks(self):
        self.assertIsNotNone(short_entry(row(breaks_down=2), down_btc(), C))
        self.assertIsNone(short_entry(row(breaks_down=1), down_btc(), C))
        self.assertIsNone(short_entry(row(breaks_down=2), up_btc(), C))

    def test_long_exit_on_trend_break_or_btc_turning_down(self):
        self.assertTrue(long_exit(row(c=90, long_exit=95), up_btc()))
        self.assertTrue(long_exit(row(c=100, long_exit=95), down_btc()))
        self.assertFalse(long_exit(row(c=100, long_exit=95), up_btc()))

    def test_short_exit_on_trend_break_or_btc_turning_up(self):
        self.assertTrue(short_exit(row(c=110, short_exit=105), down_btc()))
        self.assertTrue(short_exit(row(c=100, short_exit=105), up_btc()))
        self.assertFalse(short_exit(row(c=100, short_exit=105), down_btc()))


class ShortWindowLongModelTests(unittest.TestCase):
    """ShortWindowLongModel is the live-deployable, long-only wrapper around
    the same signal tested via run_symmetric/evaluate (Part 1 of the
    long+short roadmap) -- it must produce identical entry/exit decisions
    to the standalone long_entry/long_exit functions it wraps."""

    def test_long_stop_matches_atr_multiplier_below_close(self):
        f = row(c=100.0, atr=2.0)
        self.assertEqual(long_stop(f, C), 100.0 - C["atr_multiplier"] * 2.0)

    def test_buy_true_only_when_long_entry_would_fire(self):
        self.assertTrue(ShortWindowLongModel.buy(row(breaks_up=2), up_btc(), C))
        self.assertFalse(ShortWindowLongModel.buy(row(breaks_up=1), up_btc(), C))
        self.assertFalse(ShortWindowLongModel.buy(row(breaks_up=2), down_btc(), C))

    def test_sell_matches_long_exit(self):
        self.assertEqual(ShortWindowLongModel.sell(row(c=90, long_exit=95), up_btc()),
                          long_exit(row(c=90, long_exit=95), up_btc()))
        self.assertEqual(ShortWindowLongModel.sell(row(c=100, long_exit=95), up_btc()),
                          long_exit(row(c=100, long_exit=95), up_btc()))

    def test_features_delegates_to_symmetric_features(self):
        n = 60
        rows = [dict(t=i * INTERVAL, o=100.0, h=100.5, l=99.5, c=100.0, v=100.0) for i in range(n)]
        out = ShortWindowLongModel.features(rows, C)
        self.assertEqual(len(out), n)
        self.assertIn("breaks_up", out[-1])
        self.assertIn("long_exit", out[-1])


class RunSymmetricTests(unittest.TestCase):
    def test_flat_data_produces_no_trades_and_does_not_crash(self):
        n = 200
        rows = [dict(t=i * INTERVAL, o=100.0, h=100.5, l=99.5, c=100.0, v=100.0) for i in range(n)]
        data = {'BTCUSDT': rows}
        state = run_symmetric(data, ['BTCUSDT'], C, start=0, end=n * INTERVAL)
        self.assertEqual(state['trades'], [])


class EvaluateTests(unittest.TestCase):
    def test_runs_without_crashing_on_flat_data_and_reports_no_go(self):
        n = 2000  # 15m rows -> ~125 four-hour bars after internal aggregation
        rows = [dict(t=i * INTERVAL, o=100.0, h=100.5, l=99.5, c=100.0, v=100.0) for i in range(n)]
        data = {'BTCUSDT': rows}
        manifest = dict(start=0, end=n * INTERVAL, symbols=['BTCUSDT'])
        result = evaluate(data, ['BTCUSDT'], C, manifest, count=2, min_trades=1, warm=10)
        self.assertEqual(result['verdict'], 'NO_GO')
        self.assertEqual(len(result['windows']), 2)


if __name__ == '__main__':
    unittest.main()
