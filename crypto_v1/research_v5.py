"""User-requested exploratory variant (17 Sep 2026): symmetric LONG and
SHORT signals with a SHORTER Donchian-style lookback (10/20/40 four-hour
bars instead of V2's 20/40/80) for faster reaction, per the user's
explicit preference for shorter-duration signals over waiting for a
long-established trend.

No leverage -- signal quality is tested unleveraged first, same
sequencing agreed for every other variant. Short mechanics here also do
NOT model borrow cost or funding rate (a real Binance Margin/Futures
short pays these); this tests signal quality only, not what a real short
position would net after those costs -- that comes in a later phase if
this passes.

This is a standalone simulator (not crypto_v1.backtest.Engine, which is
long-only) so it can hold a short qty for a symbol without touching the
already-validated long-only engine used by V1/V2 live trading.
"""
from .indicators import features
from .backtest import metrics
from .research_v2 import aggregate, FOUR_HOUR
from . import walkforward as wf

WINDOWS = (10, 20, 40)  # four-hour bars: ~1.7 / 3.3 / 6.7 days -- about half of V2's 20/40/80
COST_HURDLE = 0.006  # expected 2R move must clear this fraction of price, same as V2


def btc_up_ok(btc):
    return bool(btc and btc.get("ema200") and btc["c"] > btc["ema20"] > btc["ema50"] > btc["ema200"])


def btc_down_ok(btc):
    return bool(btc and btc.get("ema200") and btc["c"] < btc["ema20"] < btc["ema50"] < btc["ema200"])


def symmetric_features(rows, config):
    out = features(rows, config)
    for i, row in enumerate(out):
        up = down = 0
        for period in WINDOWS:
            if i >= period:
                window = rows[i - period:i]
                if row["c"] > max(x["h"] for x in window):
                    up += 1
                if row["c"] < min(x["l"] for x in window):
                    down += 1
        row["breaks_up"], row["breaks_down"] = up, down
        mid = WINDOWS[1]
        row["long_exit"] = min(x["l"] for x in rows[i - mid:i]) if i >= mid else None
        row["short_exit"] = max(x["h"] for x in rows[i - mid:i]) if i >= mid else None
    return out


def long_entry(row, btc, config):
    if not btc_up_ok(btc) or not row.get("atr") or row.get("breaks_up", 0) < 2:
        return None
    stop = row["c"] - config["atr_multiplier"] * row["atr"]
    if stop <= 0 or 2 * (row["c"] - stop) / row["c"] < COST_HURDLE:
        return None
    return stop


def short_entry(row, btc, config):
    if not btc_down_ok(btc) or not row.get("atr") or row.get("breaks_down", 0) < 2:
        return None
    stop = row["c"] + config["atr_multiplier"] * row["atr"]
    if 2 * (stop - row["c"]) / row["c"] < COST_HURDLE:
        return None
    return stop


def long_exit(row, btc):
    return not btc_up_ok(btc) or row.get("long_exit") is None or row["c"] < row["long_exit"]


def short_exit(row, btc):
    return not btc_down_ok(btc) or row.get("short_exit") is None or row["c"] > row["short_exit"]


def run_symmetric(data, symbols, c, start, end):
    prepared = {s: {r["t"]: r for r in symmetric_features(rows, c)} for s, rows in data.items()}
    times = sorted(t for t in prepared.get("BTCUSDT", {}) if start <= t < end)
    cash = c["initial_cash"]
    fee, slippage = c["fee"], c["slippage"]
    positions = {}
    pending = {}
    trades, curve = [], []
    marks = {}

    def close(symbol, price, t, reason):
        p = positions.pop(symbol)
        if p["side"] == "long":
            fill = price * (1 - slippage)
            received = p["qty"] * fill * (1 - fee)
            pnl = received - p["qty"] * p["entry"] * (1 + fee)
            cash_delta = received
        else:
            fill = price * (1 + slippage)
            cost = p["qty"] * fill * (1 + fee)
            pnl = p["qty"] * p["entry"] * (1 - fee) - cost
            cash_delta = pnl  # margin-free approximation: only the P&L moves cash
        return fill, pnl, cash_delta, p

    for t in times:
        bars = {s: prepared[s][t] for s in prepared if t in prepared[s]}
        marks.update({s: f["o"] for s, f in bars.items()})
        for symbol, side in list(pending.items()):
            if symbol in positions or symbol not in bars:
                continue
            f = bars[symbol]
            entry = f["o"] * (1 + slippage) if side == "long" else f["o"] * (1 - slippage)
            equity = cash + sum(
                (marks.get(s, p["entry"]) - p["entry"]) * p["qty"] * (1 if p["side"] == "long" else -1)
                for s, p in positions.items()
            )
            notional = equity * c["risk_fraction"] / 0.02  # ~fixed fractional exposure, matches V2 scale
            qty = min(notional, cash * 0.9) / entry if entry else 0
            if qty * entry >= 10 and len(positions) < c["max_positions"]:
                stop = long_entry(f, bars.get("BTCUSDT"), c) if side == "long" else short_entry(f, bars.get("BTCUSDT"), c)
                if stop is not None:
                    cash -= qty * entry * fee  # only fee leaves cash; notional itself is tracked via mark-to-market
                    positions[symbol] = dict(side=side, qty=qty, entry=entry, stop=stop, entry_time=t)
        pending = {}
        for symbol, p in list(positions.items()):
            if symbol not in bars:
                continue
            f = bars[symbol]
            hit_stop = (f["l"] <= p["stop"]) if p["side"] == "long" else (f["h"] >= p["stop"])
            if hit_stop:
                fill, pnl, cash_delta, pos = close(symbol, p["stop"], t, "stop")
                cash += cash_delta if pos["side"] == "long" else 0
                if pos["side"] == "short":
                    cash += pnl
                trades.append(dict(symbol=symbol, side=pos["side"], entry_time=pos["entry_time"], exit_time=t,
                                    entry=pos["entry"], exit=fill, qty=pos["qty"], pnl=pnl,
                                    risk=abs(pos["entry"] - pos["stop"]) * pos["qty"],
                                    r_multiple=pnl / (abs(pos["entry"] - pos["stop"]) * pos["qty"] or 1), reason="stop"))
        for symbol, p in list(positions.items()):
            if symbol not in bars:
                continue
            f = bars[symbol]
            exited = long_exit(f, bars.get("BTCUSDT")) if p["side"] == "long" else short_exit(f, bars.get("BTCUSDT"))
            if exited:
                fill, pnl, cash_delta, pos = close(symbol, f["c"], t, "trend_exit")
                cash += cash_delta if pos["side"] == "long" else 0
                if pos["side"] == "short":
                    cash += pnl
                trades.append(dict(symbol=symbol, side=pos["side"], entry_time=pos["entry_time"], exit_time=t,
                                    entry=pos["entry"], exit=fill, qty=pos["qty"], pnl=pnl,
                                    risk=abs(pos["entry"] - pos["stop"]) * pos["qty"],
                                    r_multiple=pnl / (abs(pos["entry"] - pos["stop"]) * pos["qty"] or 1), reason="trend_exit"))
        marks.update({s: f["c"] for s, f in bars.items()})
        for symbol in symbols:
            f = bars.get(symbol)
            if not f or symbol in positions or symbol in pending:
                continue
            if long_entry(f, bars.get("BTCUSDT"), c) is not None:
                pending[symbol] = "long"
            elif short_entry(f, bars.get("BTCUSDT"), c) is not None:
                pending[symbol] = "short"
        equity = cash + sum(
            (marks.get(s, p["entry"]) - p["entry"]) * p["qty"] * (1 if p["side"] == "long" else -1)
            for s, p in positions.items()
        )
        curve.append(dict(time=t + FOUR_HOUR, equity=equity, positions=len(positions)))
    return dict(trades=trades, curve=curve, positions={})


def evaluate(data, symbols, c, manifest, count=5, min_trades=8, warm=100):
    """data: native 15m rows (as loaded by crypto_v1.data.load) -- aggregated
    to 4h internally, so the same cached 15m dataset used by every other
    variant here can be reused without a new fetch."""
    agg_data = {symbol: aggregate(rows, FOUR_HOUR) for symbol, rows in data.items()}
    symbols = [s for s in symbols if len(agg_data.get(s, [])) >= 200]
    times = [r["t"] for r in agg_data["BTCUSDT"] if manifest["start"] <= r["t"] < manifest["end"]]
    min_bars = warm + count * min_trades * 4
    if len(times) < min_bars:
        raise ValueError(f"At least {min_bars} BTC candles required")
    starts = wf.windows_from_times(times, count, warm=warm, min_trades=min_trades) + [manifest["end"]]
    windows = []
    for i in range(count):
        start, end = starts[i], starts[i + 1]
        normal = metrics(run_symmetric(agg_data, symbols, c, start, end), c)
        cs = wf.stress_config(c)
        stress = metrics(run_symmetric(agg_data, symbols, cs, start, end), cs)
        ok = (normal["trade_count"] or 0) >= min_trades
        passed = ok and normal["net_return"] > 0 and (normal["profit_factor"] or 0) >= 1.0 \
            and stress["net_return"] > 0 and (stress["profit_factor"] or 0) >= 1.0
        windows.append(dict(window=i, start=start, end=end, enough_trades=ok,
                             normal=normal, stress=stress, passed=passed))
    verdict = "GO" if windows and all(w["passed"] for w in windows) else "NO_GO"
    reasons = [] if verdict == "GO" else [
        f"window {w['window']}: " + (
            "too few trades" if not w["enough_trades"] else
            f"normal net_return={w['normal']['net_return']:.4f} PF={w['normal']['profit_factor']}, "
            f"stress net_return={w['stress']['net_return']:.4f} PF={w['stress']['profit_factor']}")
        for w in windows if not w["passed"]]
    return dict(verdict=verdict, reasons=reasons, window_count=count, windows=windows)
