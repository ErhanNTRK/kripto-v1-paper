"""Detect live short candidates from the already-validated v5 short signal
and size their leverage by signal strength, per the user's 18 Sep 2026
instruction: 3x for a normal breakout confirmation, 5x for a strong one."""
from .research_v5 import short_entry, symmetric_features

LEVERAGE_NORMAL = 3
LEVERAGE_STRONG = 5
STRONG_BREAKS_DOWN = 3  # all three Donchian windows (10/20/40) broke down at once


def leverage_for_signal(breaks_down):
    return LEVERAGE_STRONG if breaks_down >= STRONG_BREAKS_DOWN else LEVERAGE_NORMAL


def detect_short_candidates(agg_data, symbols, config):
    """agg_data: {symbol: [4h rows]} already aggregated (see research_v2.aggregate).
    Returns candidates for the CURRENT (last) closed bar only -- this is
    called once per cron tick against freshly fetched data, mirroring how
    paper_trading.tick() surfaces one AL_ADAYI per tick for the long side."""
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
        stop = short_entry(row, btc, config)
        if stop is None:
            continue
        breaks_down = row.get("breaks_down", 0)
        candidates.append({
            "symbol": symbol,
            "time": row["t"],
            "close": row["c"],
            "stop": stop,
            "breaks_down": breaks_down,
            "leverage": leverage_for_signal(breaks_down),
        })
    return candidates
