import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from crypto_v1.data import validate, INTERVAL
from crypto_v1.indicators import features, ema
from crypto_v1.risk import size_position, validate_config
from crypto_v1.backtest import Engine, fresh_state, metrics, run
from crypto_v1.strategy import buy_signal
from crypto_v1.paper_trading import tick

C = json.loads((Path(__file__).parents[1]/'config.json').read_text())


def bar(t=0, **kw):
    f = dict(t=t, o=100, h=101, l=99, c=100, v=200, atr=1,
             ema20=98, ema50=96, ema200=90, rsi=60,
             volume_avg=100, resistance=99, support=98)
    f.update(kw)
    return f


def positioned():
    e = Engine(C, ['BTCUSDT'])
    p = size_position(10000, 10000, 100, 98, C)
    p['entry_time'] = 0
    e.s['positions']['BTCUSDT'] = p
    e.s['cash'] -= p['qty']*100*(1+C['fee'])
    e.s['marks']['BTCUSDT'] = 100
    return e


class Tests(unittest.TestCase):
    def test_risk_includes_costs_and_target(self):
        p = size_position(10000, 10000, 100, 98, C)
        self.assertAlmostEqual(p['initial_risk'], 50)
        profit = p['qty']*(p['target']*(1-C['slippage'])*(1-C['fee'])-100*(1+C['fee']))
        self.assertAlmostEqual(profit, 100)

    def test_cash_cap_and_invalid_stop(self):
        p = size_position(10000, 100, 100, 98, C)
        self.assertLessEqual(p['qty']*100*(1+C['fee']), 100.00000001)
        self.assertIsNone(size_position(10000, 10000, 100, 101, C))

    def test_invalid_risk(self):
        with self.assertRaises(ValueError):
            validate_config(dict(C, risk_fraction=.006))

    def test_both_stop_and_target_stop_first(self):
        e = positioned()
        e.step(0, {'BTCUSDT': bar(h=110, l=95)})
        self.assertEqual(e.s['trades'][0]['reason'], 'stop')
        self.assertAlmostEqual(e.s['trades'][0]['pnl'], -50)

    def test_gap_loss_can_exceed_budget(self):
        e = positioned()
        e.step(0, {'BTCUSDT': bar(o=90, h=100, l=89)})
        self.assertLess(e.s['trades'][0]['pnl'], -50)
        self.assertAlmostEqual(e.s['trades'][0]['exit'], 90*(1-C['slippage']))

    def test_signal_executes_next_bar(self):
        e = Engine(C, ['BTCUSDT'])
        e.step(0, {'BTCUSDT': bar()})
        self.assertEqual(len(e.s['positions']), 0)
        e.step(INTERVAL, {'BTCUSDT': bar(t=INTERVAL)})
        self.assertEqual(e.s['positions']['BTCUSDT']['entry_time'], INTERVAL)

    def test_trailing_only_next_bar(self):
        e = positioned()
        e.s['positions']['BTCUSDT']['target'] = 200
        e.step(0, {'BTCUSDT': bar(h=104, l=99, c=103)})
        self.assertEqual(e.s['positions']['BTCUSDT']['stop'], 102)
        self.assertEqual(len(e.s['trades']), 0)
        e.step(INTERVAL, {'BTCUSDT': bar(t=INTERVAL, o=103, h=104, l=101, c=103)})
        self.assertEqual(e.s['trades'][0]['reason'], 'stop')

    def test_three_losses_and_new_day(self):
        e = positioned()
        e.s.update(day='1970-01-01', losses=2)
        e.step(0, {'BTCUSDT': bar(l=97)})
        self.assertTrue(e.s['halted'])
        self.assertFalse(e.s['pending_buys'])
        e.s['last_t'] = 86400000-INTERVAL
        e.step(86400000, {'BTCUSDT': bar(t=86400000)})
        self.assertFalse(e.s['halted'])

    def test_daily_loss_liquidation(self):
        e = positioned()
        e.step(0, {'BTCUSDT': bar(o=80, l=79, h=81, c=80)})
        self.assertTrue(e.s['halted'])
        self.assertEqual(e.s['trades'][0]['reason'], 'daily_loss')

    def test_restart_idempotency(self):
        e = Engine(C, ['BTCUSDT'])
        e.step(0, {'BTCUSDT': bar()})
        clone = Engine(C, ['BTCUSDT'], json.loads(json.dumps(e.s)))
        clone.step(0, {'BTCUSDT': bar()})
        self.assertEqual(clone.s, e.s)
        for engine in (e, clone):
            engine.step(INTERVAL, {'BTCUSDT': bar(t=INTERVAL)})
        self.assertEqual(clone.s, e.s)

    def test_missing_bar_fails_closed(self):
        e = Engine(C, ['BTCUSDT'])
        e.step(0, {'BTCUSDT': bar()})
        with self.assertRaises(ValueError):
            e.step(2*INTERVAL, {'BTCUSDT': bar()})

    def test_indicators_no_future_leak(self):
        rows = [dict(t=i*INTERVAL, o=100+i*.1, h=101+i*.1, l=99+i*.1, c=100+i*.1, v=100+i) for i in range(250)]
        a = features(rows, C)
        rows[220]['c'] = 10000
        b = features(rows, C)
        self.assertEqual(a[:220], b[:220])
        self.assertEqual(a[200]['resistance'], max(r['h'] for r in rows[180:200]))
        self.assertAlmostEqual(ema([1,2,3,4], 3)[-1], 3)

    def test_filters(self):
        self.assertTrue(buy_signal(bar(), bar(), C))
        self.assertFalse(buy_signal(bar(rsi=71), bar(), C))
        self.assertFalse(buy_signal(bar(), bar(c=80), C))
        self.assertFalse(buy_signal(bar(v=149), bar(), C))
        self.assertFalse(buy_signal(bar(c=99), bar(), C))

    def test_metrics(self):
        state = fresh_state(C)
        state['trades'] = [dict(pnl=100, r_multiple=2), dict(pnl=-50, r_multiple=-1)]
        state['curve'] = [dict(equity=10100), dict(equity=10050)]
        m = metrics(state, C)
        self.assertEqual(m['profit_factor'], 2)
        self.assertEqual(m['expectancy_usdt'], 25)
        self.assertEqual(m['win_rate'], .5)
        self.assertAlmostEqual(m['max_drawdown'], 50/10100)

    def test_paper_tick_no_retro_trades_or_duplicates(self):
        rows = [dict(t=i*INTERVAL, o=100, h=101, l=99, c=100, v=100) for i in range(210)]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'manifest.json').write_text(json.dumps(dict(symbols=['BTCUSDT'], start=0)))
            (root/'BTCUSDT.json').write_text(json.dumps(rows))
            with patch('crypto_v1.paper_trading.get', return_value={'serverTime':210*INTERVAL}), patch('crypto_v1.paper_trading.candles', return_value=[]):
                tick(C, root, root/'state.json', root/'reports')
                first = json.loads((root/'state.json').read_text())
                tick(C, root, root/'state.json', root/'reports')
                self.assertEqual(first, json.loads((root/'state.json').read_text()))
                self.assertEqual(first['state']['trades'], [])

    def test_validate_data(self):
        with self.assertRaises(ValueError):
            validate([bar(), bar(t=2*INTERVAL)])

    def test_nonfinite_data_and_config(self):
        with self.assertRaises(ValueError):
            validate([bar(v=float('nan'))])
        with self.assertRaises(ValueError):
            validate_config(dict(C, min_reward_risk=float('nan')))

    def test_shared_cash_and_position_limit(self):
        symbols = ['BTCUSDT', 'AUSDT', 'BUSDT', 'CUSDT', 'DUSDT']
        e = Engine(C, symbols)
        e.step(0, {symbol: bar() for symbol in symbols})
        e.step(INTERVAL, {symbol: bar(t=INTERVAL) for symbol in symbols})
        self.assertEqual(len(e.s['positions']), 3)
        self.assertGreaterEqual(e.s['cash'], -1e-8)
        self.assertTrue(all(p['initial_risk'] <= 50 for p in e.s['positions'].values()))


if __name__ == '__main__':
    unittest.main()
