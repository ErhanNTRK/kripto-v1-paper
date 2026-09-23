import csv
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from .data import INTERVAL
from .indicators import features
from .strategy import buy_signal, sell_signal, initial_stop
from .risk import size_position
from .telegram import export_outbox


def fresh_state(c):
    return dict(cash=c['initial_cash'], positions={}, pending_buys={}, pending_sells={},
                day=None, day_equity=c['initial_cash'], halted=False, losses=0,
                last_t=None, marks={}, trades=[], curve=[], events=[])


class Engine:
    def __init__(self, config, symbols, state=None, model=None, interval=INTERVAL):
        self.c = config
        self.symbols = symbols
        self.model = model
        self.interval = interval
        self.s = state if state is not None else fresh_state(config)

    def equity(self):
        return self.s['cash'] + sum(p['qty'] * self.s['marks'][s] for s, p in self.s['positions'].items())

    def close(self, symbol, price, t, reason):
        s, c = self.s, self.c
        p = s['positions'].pop(symbol)
        fill = price * (1 - c['slippage'])
        received = p['qty'] * fill * (1 - c['fee'])
        pnl = received - p['qty'] * p['entry'] * (1 + c['fee'])
        s['cash'] += received
        trade = dict(symbol=symbol, entry_time=p['entry_time'], exit_time=t,
                     entry=p['entry'], exit=fill, qty=p['qty'], pnl=pnl,
                     risk=p['initial_risk'], r_multiple=pnl / p['initial_risk'], reason=reason)
        s['trades'].append(trade)
        s['events'].append(dict(type='SAT', time=t, symbol=symbol, price=fill, reason=reason, simulated=True))
        s['losses'] = s['losses'] + 1 if pnl < 0 else 0
        if s['losses'] >= c['max_consecutive_losses']:
            s['halted'] = True

    def partial_close(self, symbol, price, t, fraction):
        """Take `fraction` of a position off at `price` and keep the rest
        running (research option partial_take_r, 23 Sep 2026). Booked as its
        own trade with its own share of the initial risk, so R-multiples and
        profit factor stay comparable with whole-position exits."""
        s, c = self.s, self.c
        p = s['positions'][symbol]
        qty = p['qty'] * fraction
        fill = price * (1 - c['slippage'])
        received = qty * fill * (1 - c['fee'])
        pnl = received - qty * p['entry'] * (1 + c['fee'])
        risk = p['initial_risk'] * fraction
        s['cash'] += received
        p['qty'] -= qty
        p['initial_risk'] -= risk
        p['partial_done'] = True
        s['trades'].append(dict(symbol=symbol, entry_time=p['entry_time'], exit_time=t, entry=p['entry'],
                                exit=fill, qty=qty, pnl=pnl, risk=risk, r_multiple=pnl / risk if risk else 0,
                                reason='partial_take'))

    def step(self, t, bars):
        s, c = self.s, self.c
        if s['last_t'] is not None and t <= s['last_t']:
            return
        if s['last_t'] is not None and t != s['last_t'] + self.interval:
            raise ValueError('Missing portfolio bar; refusing to skip time')
        if 'BTCUSDT' not in bars or any(symbol not in bars for symbol in s['positions']):
            raise ValueError('Missing BTC or open-position candle')
        day = datetime.fromtimestamp(t / 1000, timezone.utc).date().isoformat()
        if day != s['day']:
            s.update(day=day, day_equity=self.equity(), halted=False, losses=0)
        s['marks'].update({symbol: f['o'] for symbol, f in bars.items()})
        for symbol, reason in list(s['pending_sells'].items()):
            if symbol in s['positions']:
                self.close(symbol, bars[symbol]['o'], t, reason)
        s['pending_sells'] = {}
        if self.equity() <= s['day_equity'] * (1-c['daily_loss_fraction']):
            s['halted'] = True
            for symbol in list(s['positions']):
                self.close(symbol, bars[symbol]['o'], t, 'daily_loss')
        # Rank simultaneous candidates by volume anomaly, then symbol; cash is shared.
        candidates = sorted(s['pending_buys'].items(), key=lambda x: (-x[1]['score'], x[0]))
        # entry_pullback_atr (default 0 = the original behavior: take the next
        # bar's open, i.e. buy the breakout itself). Above 0, the entry is a
        # LIMIT that ATR-fraction below the signal bar's close, good for
        # entry_pullback_bars bars -- "wait for the retest" instead of
        # chasing. A candidate that never pulls back is simply skipped.
        pullback = Decimal(str(c.get('entry_pullback_atr', 0) or 0))
        keep = {}
        for symbol, candidate in candidates:
            if s['halted'] or len(s['positions']) >= c['max_positions']:
                break
            if symbol in s['positions'] or symbol not in bars:
                continue
            bar = bars[symbol]
            if pullback > 0:
                limit = candidate.get('limit')
                if limit is None or t > candidate.get('expires_at', t):
                    continue
                if bar['l'] > limit:
                    keep[symbol] = candidate  # no retest yet; still waiting
                    continue
                entry = min(bar['o'], limit) * (1 + c['slippage'])
            else:
                entry = bar['o'] * (1 + c['slippage'])
            p = size_position(self.equity(), s['cash'], entry, candidate['stop'], c)
            if p:
                p['entry_time'] = t
                s['cash'] -= p['qty'] * entry * (1+c['fee'])
                s['positions'][symbol] = p
                s['events'].append(dict(type='AL', time=t, symbol=symbol, price=entry,
                                        stop=p['stop'], target=p['target'], risk=p['initial_risk'], simulated=True))
        s['pending_buys'] = keep
        for symbol, p in list(s['positions'].items()):
            f = bars[symbol]
            if f['l'] <= p['stop']:
                self.close(symbol, min(f['o'], p['stop']), t, 'stop')
            elif c.get('cap_at_target', True) and f['h'] >= p['target']:
                self.close(symbol, p['target'], t, 'target_2R')
            elif c.get('partial_take_r') and not p.get('partial_done')                     and f['h'] >= p['entry'] + c['partial_take_r'] * p['unit_risk']:
                self.partial_close(symbol, p['entry'] + c['partial_take_r'] * p['unit_risk'], t,
                                   float(c.get('partial_take_fraction', 0.5)))
        s['marks'].update({symbol: f['c'] for symbol, f in bars.items()})
        if self.equity() <= s['day_equity'] * (1-c['daily_loss_fraction']):
            s['halted'] = True
            s['pending_sells'].update({symbol: 'daily_loss' for symbol in s['positions']})
        btc = bars['BTCUSDT']
        for symbol, p in s['positions'].items():
            f = bars[symbol]
            sold = self.model.sell(f, btc, c) if self.model else sell_signal(f, btc, c)
            if sold:
                s['pending_sells'].setdefault(symbol, 'trend_exit')
            p['high'] = max(p['high'], f['h'])
            if p['high'] >= p['entry'] + p['unit_risk']:
                # End-of-bar update: new trailing stop is effective on next bar only.
                p['stop'] = max(p['stop'], p['high'] - c['trailing_atr'] * f['atr'])
        if not s['halted']:
            for symbol in self.symbols:
                f = bars.get(symbol)
                buy = self.model.buy if self.model else buy_signal
                stop = self.model.stop if self.model else initial_stop
                if symbol not in s['positions'] and f and buy(f, btc, c):
                    # breaks_up (v5 models only; absent -> 0) lets the live
                    # leveraged-long path (21 Sep 2026) size leverage by
                    # signal strength without this module importing
                    # short_signal.leverage_for_signal, which would create
                    # backtest -> short_signal -> research_v5 -> backtest,
                    # a circular import (research_v5 imports metrics from
                    # here). The live controller does that lookup instead.
                    waiting = s['pending_buys'].get(symbol, {})
                    # volume_avg is None for a symbol's first 20 bars (a coin
                    # listed mid-test); rank it last instead of crashing.
                    volume_ratio = f['v'] / f['volume_avg'] if f.get('volume_avg') else 0
                    entry_plan = dict(stop=stop(f, c), score=f.get('signal_score', volume_ratio),
                                      breaks_up=f.get('breaks_up', 0))
                    if c.get('entry_pullback_atr', 0):
                        # A fresh signal restarts the window at the new level.
                        entry_plan['limit'] = f['c'] - c['entry_pullback_atr'] * f['atr']
                        entry_plan['expires_at'] = t + int(c.get('entry_pullback_bars', 2)) * self.interval
                    elif waiting:
                        entry_plan = waiting
                    s['pending_buys'][symbol] = entry_plan
                    s['events'].append(dict(type='AL_ADAYI', time=t+self.interval, symbol=symbol,
                                            close=f['c'], simulated=True))
        s['last_t'] = t
        s['curve'].append(dict(time=t+self.interval, equity=self.equity(), positions=len(s['positions']), halted=s['halted']))


def prepare(data, c, feature_fn=features):
    return {symbol: {r['t']: r for r in feature_fn(rows, c)} for symbol, rows in data.items()}


def run(data, symbols, c, start=None, end=None, model=None, interval=INTERVAL):
    prepared = prepare(data, c, model.features if model else features)
    engine = Engine(c, symbols, model=model, interval=interval)
    times = sorted(prepared['BTCUSDT'])
    for t in times:
        if (start is not None and t < start) or (end is not None and t >= end):
            continue
        bars = {s: d[t] for s, d in prepared.items() if t in d}
        engine.step(t, bars)
    s = engine.s
    if s['last_t'] is not None:
        for symbol in list(s['positions']):
            engine.close(symbol, s['marks'][symbol], s['last_t']+interval, 'end_of_test')
        s['curve'].append(dict(time=s['last_t']+interval, equity=engine.equity(), positions=0, halted=s['halted']))
    return s


def metrics(state, c):
    trades = state['trades']
    n = len(trades)
    wins = sum(t['pnl'] > 0 for t in trades)
    profit = sum(max(0, t['pnl']) for t in trades)
    loss = -sum(min(0, t['pnl']) for t in trades)
    peak, drawdown = c['initial_cash'], 0
    for point in state['curve']:
        peak = max(peak, point['equity'])
        drawdown = max(drawdown, 1-point['equity']/peak)
    final = state['curve'][-1]['equity'] if state['curve'] else c['initial_cash']
    return dict(trade_count=n, win_rate=wins/n if n else None,
                expectancy_usdt=(profit-loss)/n if n else None,
                expectancy_R=sum(t['r_multiple'] for t in trades)/n if n else None,
                max_drawdown=drawdown, profit_factor=profit/loss if loss else None,
                profit_factor_note='undefined: no losing trades' if not loss else '',
                net_return=final/c['initial_cash']-1, final_equity=final,
                open_positions=len(state['positions']), costs_included=True)


def report(state, c, metadata, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    result = dict(metadata=metadata, config=c, metrics=metrics(state, c))
    (output/'report.json').write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    for name, rows in [('trades', state['trades']), ('equity', state['curve'])]:
        with (output/(name+'.csv')).open('w', newline='', encoding='utf-8') as f:
            fields = list(rows[0]) if rows else (['symbol','pnl'] if name == 'trades' else ['time','equity'])
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    export_outbox(state['events'], output/'telegram_outbox.jsonl')
    m = result['metrics']
    def fmt(v, percent=False):
        return 'N/A' if v is None else (f'{v:.2%}' if percent else f'{v:.4f}')
    lines = ['# Kripto V1 sonuc raporu', '', f"Mod: {metadata.get('mode')}",
             f"Islem sayisi: {m['trade_count']}", f"Win rate: {fmt(m['win_rate'], True)}",
             f"Expectancy: {fmt(m['expectancy_usdt'])} USDT / {fmt(m['expectancy_R'])} R",
             f"Max drawdown (mum kapanisi): {fmt(m['max_drawdown'], True)}",
             f"Profit factor: {fmt(m['profit_factor'])}", f"Net return: {fmt(m['net_return'], True)}",
             f"Son portfoy: {fmt(m['final_equity'])} USDT", '',
             'Komisyon ve kayma dahil. Sonuclar kar garantisi veya canli emir onayi degildir.',
             'Guncel top-50 evreni gecmise uygulanir; secim ve hayatta kalma yanliligi vardir.',
             'Stop bosluklari nedeniyle gerceklesen zarar planlanan %0,5 riski asabilir.',
             '', '```json', json.dumps(metadata, indent=2, ensure_ascii=False), '```']
    (output/'RAPOR.md').write_text('\n'.join(lines), encoding='utf-8')
    return result
