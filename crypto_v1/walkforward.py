"""Walk-forward validation: run a config across several independent, non-overlapping
historical windows (each its own fresh-capital simulation) plus a 2x-cost stress
variant of each window, and render an explicit GO / NO-GO verdict.

Rationale: a single 90-day backtest with one holdout split can look profitable by
chance. Requiring consistent profitability across multiple separate time windows,
each also surviving doubled fees/slippage, is a much harder bar to clear by luck
and is standard practice before risking real capital.
"""
import json
from pathlib import Path
from .backtest import run, metrics

MIN_TRADES_PER_WINDOW = 30


def stress_config(c):
    c2 = dict(c)
    c2['fee'] = min(c2['fee'] * 2, 0.019)
    c2['slippage'] = min(c2['slippage'] * 2, 0.019)
    return c2


def windows_from_times(times, count):
    usable = times[200:]  # skip indicator warm-up, matches crypto_v1.backtest CLI
    if count < 2:
        raise ValueError('Need at least 2 windows for walk-forward validation')
    if len(usable) < count * MIN_TRADES_PER_WINDOW * 4:
        raise ValueError('Not enough candles for the requested window count')
    chunk = len(usable) // count
    return [usable[i * chunk] for i in range(count)]


def evaluate(data, symbols, c, manifest, count=4):
    times = [r['t'] for r in data['BTCUSDT'] if manifest['start'] <= r['t'] < manifest['end']]
    if len(times) < 400:
        raise ValueError('At least 400 BTC candles required')
    starts = windows_from_times(times, count) + [manifest['end']]
    windows = []
    for i in range(count):
        start, end = starts[i], starts[i + 1]
        normal = metrics(run(data, symbols, c, start, end), c)
        cs = stress_config(c)
        stress = metrics(run(data, symbols, cs, start, end), cs)
        ok = (normal['trade_count'] or 0) >= MIN_TRADES_PER_WINDOW
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


def report(result, metadata, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    full = dict(metadata=metadata, **result)
    (output / 'summary.json').write_text(
        json.dumps(full, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    lines = ['# Walk-forward dogrulama', '', f"Config: {metadata.get('config_name')}",
             f"## Sonuc: {result['verdict']}", '']
    if result['reasons']:
        lines += ['Gerekce:'] + [f"- {r}" for r in result['reasons']] + ['']
    lines += ['| Pencere | Islem | Net getiri | PF | Stres net getiri | Stres PF | Gecti mi |',
              '|---:|---:|---:|---:|---:|---:|:---:|']
    for w in result['windows']:
        n, s = w['normal'], w['stress']
        lines.append(
            f"| {w['window']} | {n['trade_count']} | {n['net_return']:.2%} | "
            f"{n['profit_factor'] or 0:.2f} | {s['net_return']:.2%} | {s['profit_factor'] or 0:.2f} | "
            f"{'EVET' if w['passed'] else 'hayir'} |")
    lines += ['', 'GO: tum pencerelerde normal VE 2x maliyet stresinde net getiri > 0 ve profit factor >= 1.0.',
               'Tek pencere sansa dayanamaz; bu yuzden hepsi gecmeli.']
    (output / 'SUMMARY.md').write_text('\n'.join(lines), encoding='utf-8')
    return full
