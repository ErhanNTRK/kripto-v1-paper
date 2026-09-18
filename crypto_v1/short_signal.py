"""Detect live long/short candidates from the already-validated v5 signal
and size short leverage by signal strength, per the user's 18 Sep 2026
instruction: 3x for a normal breakout confirmation, 5x for a strong one."""
from .research_v5 import long_entry, short_entry, symmetric_features

LEVERAGE_NORMAL = 3
LEVERAGE_STRONG = 5
STRONG_BREAKS = 3  # all three Donchian windows (10/20/40) broke out at once


def leverage_for_signal(breaks):
    return LEVERAGE_STRONG if breaks >= STRONG_BREAKS else LEVERAGE_NORMAL


def _detect(agg_data, symbols, config, entry_fn, breaks_key):
    """Shared scan: evaluate entry_fn against the CURRENT (last) closed bar
    only, for every symbol -- called once per cron tick against freshly
    fetched data. No per-symbol state/suppression once a signal has fired
    once (see live_limits.may_open's held_symbols check, which is what
    actually stops a live entry from doubling up on a symbol already
    held -- this function only detects, it never decides whether to act)."""
    if "BTCUSDT" not in agg_data:
        return []
    btc_rows = symmetric_features(agg_data["BTCUSDT"], config)
    if not btc_rows:
        return []
    btc = btc_rows[-1]
    candidates = []
    for symbol in symbols:
        rows = agg_data.get(symbol)
        if not rows or len(rows) < 200:
            continue
        features = symmetric_features(rows, config)
        row = features[-1]
        stop = entry_fn(row, btc, config)
        if stop is None:
            continue
        breaks = row.get(breaks_key, 0)
        candidates.append({
            "symbol": symbol,
            "time": row["t"],
            "close": row["c"],
            "stop": stop,
            breaks_key: breaks,
            "leverage": leverage_for_signal(breaks),
        })
    return candidates


def detect_short_candidates(agg_data, symbols, config):
    """agg_data: {symbol: [Nh rows]} already aggregated to whatever
    interval config's strategy was validated at (see research_v2.aggregate)."""
    return _detect(agg_data, symbols, config, short_entry, "breaks_down")


def detect_long_candidates(agg_data, symbols, config):
    """Mirrors detect_short_candidates for the long side. Used for the 2H
    system (18 Sep 2026): the 4H long side still detects candidates via
    paper_trading.tick()'s backtest.Engine (proven, unchanged, deliberately
    left alone) -- this lightweight, stateless scan is for the NEW 2H
    system only, so it doesn't touch the already-live 4H detection path."""
    return _detect(agg_data, symbols, config, long_entry, "breaks_up")
