"""Backtest of a friend's independently-written Telegram-confirmed signal bot,
replicated as closely as our cached data allows for a fair walk-forward check.

Original live rules (sinyal_bot.py / sinyal_bot_FUTURES_V1.py):
  - 8 fixed pairs, one position at a time, picks the 4/4 candidate with the
    highest volume ratio.
  - Entry needs all 4: 5m MA9 > MA21, 1h MA9 > MA21, RSI14(5m) in [50, 68],
    5m volume > 1.20x its 20-bar average.
  - Fixed -2% initial stop, moves to breakeven at +3%, trails 3% below peak
    once up +5%. No take-profit target, no BTC-wide filter, no daily-loss or
    consecutive-loss halt (the live bot has none of these either).
  - New entries only 09:00-23:00 Turkey time (UTC+3); open positions are
    still managed (stopped out) outside that window.

Caveat: the live bot reads native 5-minute candles. We only cache 15-minute
candles (crypto_v1.data), so this substitutes 15m bars for the 'short'
timeframe and hourly (4x15m) bars for the '1h' timeframe -- same rule logic,
coarser resolution, not a byte-for-byte replica. Position sizing here uses
25% of equity per trade (matching the live bot's MAX_BAKIYE_ORANI) instead
of its fixed 5 USDC/trade cap, which was a tiny-pilot-capital constraint,
not a strategy parameter -- with a -2% stop this still risks the same 0.5%
of equity per trade as our own V1 config, so results are comparable.
"""
from datetime import datetime, timezone, timedelta
from pathlib import Path
from .backtest import metrics
from . import walkforward as wf

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "LINKUSDT", "AVAXUSDT", "SUIUSDT"]
HOUR = 3_600_000
STOP = 0.02
BREAKEVEN_AT = 0.03
TRAIL_AT = 0.05
TRAIL_DIST = 0.03
POSITION_FRACTION = 0.25


def sma(vals, n):
    return sum(vals[-n:]) / n if len(vals) >= n else None


def rsi14(closes):
    n = 14
    if len(closes) < n + 1:
        return None
    gains = losses = 0.0
    for i in range(-n, 0):
        d = closes[i] - closes[i - 1]
        if d >= 0:
            gains += d
        else:
            losses += -d
    gains /= n
    losses /= n
    if losses == 0:
        return 100.0
    return 100 - 100 / (1 + gains / losses)


def tr_trading_hours(t):
    dt = datetime.fromtimestamp(t / 1000, timezone.utc) + timedelta(hours=3)
    return 9 <= dt.hour < 23


def build_features(rows):
    """One pass over a symbol's 15m rows: 15m MA9/21 + RSI14 + volume ratio,
    plus the latest *closed* hourly MA9/21 available as of each 15m bar
    (no lookahead: an hourly bar only counts once its hour has fully closed)."""
    closes, vols = [], []
    hourly_groups = {}
    out = {}
    hourly_closes = []
    hourly_ma = []  # (available_from_t, ma9_1h, ma21_1h), ascending
    cursor = 0
    for row in rows:
        closes.append(row['c'])
        vols.append(row['v'])
        bucket = row['t'] // HOUR * HOUR
        hourly_groups.setdefault(bucket, []).append(row)
        if len(hourly_groups[bucket]) == 4 and bucket + HOUR - 900_000 == row['t']:
            hourly_closes.append(hourly_groups[bucket][-1]['c'])
            m9, m21 = sma(hourly_closes, 9), sma(hourly_closes, 21)
            if m9 is not None and m21 is not None:
                hourly_ma.append((bucket + HOUR, m9, m21))
        ma9, ma21, r = sma(closes, 9), sma(closes, 21), rsi14(closes)
        vavg = sma(vols, 20)
        while cursor + 1 < len(hourly_ma) and hourly_ma[cursor + 1][0] <= row['t']:
            cursor += 1
        ma9_1h = ma21_1h = None
        if hourly_ma and hourly_ma[cursor][0] <= row['t']:
            _, ma9_1h, ma21_1h = hourly_ma[cursor]
        hacim_orani = row['v'] / vavg if vavg else 0
        ok = ma9 is not None and ma21 is not None and r is not None and vavg is not None and ma9_1h is not None
        puan = 0
        if ok:
            puan = sum([ma9 > ma21, ma9_1h > ma21_1h, 50 <= r <= 68, hacim_orani > 1.20])
        out[row['t']] = dict(row, signal=ok and puan == 4, hacim_orani=hacim_orani)
    return out


def run_friend(data, c, start, end):
    featured = {s: build_features(data[s]) for s in SYMBOLS if s in data}
    times = sorted(t for t in featured.get('BTCUSDT', {}) if start <= t < end)
    cash = c['initial_cash']
    fee, slippage = c['fee'], c['slippage']
    pos = None
    pending = None
    trades, curve = [], []
    marks = {}
    for t in times:
        bars = {s: featured[s][t] for s in featured if t in featured[s]}
        marks.update({s: f['o'] for s, f in bars.items()})
        if pending and pos is None and pending in bars:
            f = bars[pending]
            entry = f['o'] * (1 + slippage)
            notional = cash * POSITION_FRACTION
            qty = notional / (entry * (1 + fee))
            if qty * entry >= 10:
                cash -= qty * entry * (1 + fee)
                pos = dict(symbol=pending, qty=qty, entry=entry, stop=entry * (1 - STOP),
                           peak=entry, entry_time=t)
            pending = None
        if pos and pos['symbol'] in bars:
            f = bars[pos['symbol']]
            if f['l'] <= pos['stop']:
                fill = min(f['o'], pos['stop']) * (1 - slippage)
                received = pos['qty'] * fill * (1 - fee)
                risk = pos['qty'] * pos['entry'] * STOP
                pnl = received - pos['qty'] * pos['entry'] * (1 + fee)
                cash += received
                trades.append(dict(symbol=pos['symbol'], entry_time=pos['entry_time'], exit_time=t,
                                    entry=pos['entry'], exit=fill, qty=pos['qty'], pnl=pnl,
                                    risk=risk, r_multiple=pnl / risk if risk else 0, reason='stop'))
                pos = None
            else:
                pos['peak'] = max(pos['peak'], f['h'])
                ret = pos['peak'] / pos['entry'] - 1
                new_stop = pos['stop']
                if ret >= BREAKEVEN_AT:
                    new_stop = max(new_stop, pos['entry'])
                if ret >= TRAIL_AT:
                    new_stop = max(new_stop, pos['peak'] * (1 - TRAIL_DIST))
                pos['stop'] = new_stop
        marks.update({s: f['c'] for s, f in bars.items()})
        if pos is None and pending is None and tr_trading_hours(t):
            candidates = [(s, bars[s]) for s in bars if bars[s]['signal']]
            if candidates:
                pending = max(candidates, key=lambda x: x[1]['hacim_orani'])[0]
        equity = cash + (pos['qty'] * marks.get(pos['symbol'], pos['entry']) if pos else 0)
        curve.append(dict(time=t + 900_000, equity=equity, positions=1 if pos else 0))
    if pos:
        exit_price = marks.get(pos['symbol'], pos['entry'])
        received = pos['qty'] * exit_price * (1 - fee)
        risk = pos['qty'] * pos['entry'] * STOP
        pnl = received - pos['qty'] * pos['entry'] * (1 + fee)
        cash += received
        trades.append(dict(symbol=pos['symbol'], entry_time=pos['entry_time'], exit_time=times[-1] + 900_000 if times else 0,
                            entry=pos['entry'], exit=exit_price, qty=pos['qty'], pnl=pnl,
                            risk=risk, r_multiple=pnl / risk if risk else 0, reason='end_of_test'))
        curve.append(dict(time=(times[-1] + 900_000) if times else 0, equity=cash, positions=0))
    return dict(trades=trades, curve=curve, positions={})


def evaluate(data, c, manifest, count=5, min_trades=5):
    times = [r['t'] for r in data['BTCUSDT'] if manifest['start'] <= r['t'] < manifest['end']]
    if len(times) < 400:
        raise ValueError('At least 400 BTC candles required')
    starts = wf.windows_from_times(times, count, warm=100, min_trades=min_trades) + [manifest['end']]
    windows = []
    for i in range(count):
        start, end = starts[i], starts[i + 1]
        normal = metrics(run_friend(data, c, start, end), c)
        cs = wf.stress_config(c)
        stress = metrics(run_friend(data, cs, start, end), cs)
        ok = (normal['trade_count'] or 0) >= min_trades
        passed = ok and normal['net_return'] > 0 and (normal['profit_factor'] or 0) >= 1.0 \
            and stress['net_return'] > 0 and (stress['profit_factor'] or 0) >= 1.0
        windows.append(dict(window=i, start=start, end=end, enough_trades=ok,
                             normal=normal, stress=stress, passed=passed))
    verdict = 'GO' if windows and all(w['passed'] for w in windows) else 'NO_GO'
    reasons = [] if verdict == 'GO' else [
        f"window {w['window']}: " + (
            'too few trades' if not w['enough_trades'] else
            f"normal net_return={w['normal']['net_return']:.4f} PF={w['normal']['profit_factor']}, "
            f"stress net_return={w['stress']['net_return']:.4f} PF={w['stress']['profit_factor']}")
        for w in windows if not w['passed']]
    return dict(verdict=verdict, reasons=reasons, window_count=count, windows=windows)
