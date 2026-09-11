"""Persistent closed-candle paper simulation; never submits an exchange order."""
import hashlib
import json
import os
from pathlib import Path
from .data import get, candles, load, validate, INTERVAL
from .backtest import Engine, prepare, report, fresh_state


def atomic_json(path, value):
    path = Path(path)
    tmp = path.with_suffix('.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(value, f, indent=2, allow_nan=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def metadata(state, now, manifest):
    return dict(mode='paper_closed_candle_simulation',
                elapsed_days=max(0, now-state['started_at'])/86400000,
                execution='next-open modeled; observed after 15m close, not live fills',
                real_order_enabled=False, universe=manifest,
                assessment='At least 30 days and 100 closed trades required before review; no automatic promotion')


def tick(c, data_dir, state_path, output):
    state_path = Path(state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock = state_path.with_suffix('.lock')
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        manifest = json.loads((Path(data_dir)/'manifest.json').read_text(encoding='utf-8'))
        fingerprint = hashlib.sha256(json.dumps(dict(config=c, symbols=manifest['symbols']), sort_keys=True).encode()).hexdigest()
        now = get('time')['serverTime'] // INTERVAL * INTERVAL
        saved = None
        if state_path.exists():
            saved = json.loads(state_path.read_text(encoding='utf-8'))
            if saved['fingerprint'] != fingerprint:
                raise ValueError('Config or universe changed; use a new paper state')
            if saved['state']['last_t'] == now-INTERVAL:
                return report(saved['state'], c, metadata(saved['state'], now, manifest), output)
        _, data = load(data_dir)
        # Update the complete stored history to keep EMA seed stable across restarts.
        for symbol, rows in data.items():
            start = rows[-1]['t']+INTERVAL if rows else manifest['start']
            rows.extend(candles(symbol, start, now))
            validate(rows)
            atomic_json(Path(data_dir)/(symbol+'.json'), rows)
        prepared = prepare(data, c)
        if saved:
            engine = Engine(c, manifest['symbols'], saved['state'])
            times = range(engine.s['last_t']+INTERVAL, now, INTERVAL)
        else:
            # Start at latest closed bar with empty portfolio; do not backfill profits.
            state = fresh_state(c)
            state['started_at'] = now
            engine = Engine(c, manifest['symbols'], state)
            times = [now-INTERVAL]
        for t in times:
            bars = {s: d[t] for s, d in prepared.items() if t in d}
            engine.step(t, bars)
        atomic_json(state_path, dict(fingerprint=fingerprint, state=engine.s))
        return report(engine.s, c, metadata(engine.s, now, manifest), output)
    finally:
        os.close(fd)
        lock.unlink(missing_ok=True)
