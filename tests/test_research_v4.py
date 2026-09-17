import unittest
from crypto_v1.research_v4 import EarlyReversalModel, evaluate_walkforward
from crypto_v1.data import INTERVAL


def row(**kw):
    base = dict(t=0, o=100.0, h=101.0, l=99.0, c=100.0, v=100.0, atr=1.0,
                ema20=98.0, ema50=96.0, ema200=90.0, rsi=55.0, volume_avg=80.0)
    base.update(kw)
    return base


def btc(**kw):
    base = dict(c=100.0, ema20=98.0)
    base.update(kw)
    return base


class EarlyReversalModelTests(unittest.TestCase):
    C = {"atr_multiplier": 2.0, "support": 90}

    def test_buys_on_ema20_reclaim_without_needing_a_breakout(self):
        # Price above its own 20-bar average, RSI mid-range: no Donchian
        # break required, unlike DonchianModel.
        self.assertTrue(EarlyReversalModel.buy(row(c=100, ema20=98, support=90), btc(), self.C))

    def test_rejects_when_below_its_own_average(self):
        self.assertFalse(EarlyReversalModel.buy(row(c=96, ema20=98, support=90), btc(), self.C))

    def test_rejects_overbought_or_still_weak_rsi(self):
        self.assertFalse(EarlyReversalModel.buy(row(rsi=75, support=90), btc(), self.C))
        self.assertFalse(EarlyReversalModel.buy(row(rsi=35, support=90), btc(), self.C))

    def test_does_not_require_btc_uptrend_only_absence_of_decline(self):
        # BTC flat/sideways (not trending up, not declining >3%) still allows a buy --
        # unlike btc_ok(), which needs ema20>ema50>ema200 stacked upward.
        flat_btc = btc(c=98, ema20=100)  # ~2% below its own average, inside the floor
        self.assertTrue(EarlyReversalModel.buy(row(support=90), flat_btc, self.C))

    def test_rejects_when_btc_in_meaningful_decline(self):
        declining_btc = btc(c=90, ema20=100)  # 10% below its own average
        self.assertFalse(EarlyReversalModel.buy(row(support=90), declining_btc, self.C))

    def test_rejects_before_volume_avg_warm_up_even_if_ema20_already_exists(self):
        # ema20 lands one bar earlier than volume_avg; without this guard
        # Engine.step's default score expression (v/volume_avg) would crash
        # on None instead of just skipping the candidate.
        self.assertFalse(EarlyReversalModel.buy(row(support=90, volume_avg=None), btc(), self.C))

    def test_sell_when_price_falls_back_below_its_average(self):
        self.assertTrue(EarlyReversalModel.sell(row(c=95, ema20=98), btc()))
        self.assertFalse(EarlyReversalModel.sell(row(c=100, ema20=98), btc()))


class EvaluateWalkforwardTests(unittest.TestCase):
    def test_runs_without_crashing_on_flat_data_and_reports_no_go(self):
        n = 1700
        rows = [dict(t=i * INTERVAL, o=100.0, h=100.5, l=99.5, c=100.0, v=100.0) for i in range(n)]
        data = {'BTCUSDT': rows}
        manifest = dict(start=0, end=n * INTERVAL, symbols=['BTCUSDT'])
        import json
        from pathlib import Path
        c = json.loads((Path(__file__).parents[1] / 'config_v2.json').read_text())
        result = evaluate_walkforward(data, ['BTCUSDT'], c, manifest, count=2, min_trades=1, warm=10)
        self.assertEqual(result['verdict'], 'NO_GO')
        self.assertEqual(len(result['windows']), 2)


if __name__ == '__main__':
    unittest.main()
