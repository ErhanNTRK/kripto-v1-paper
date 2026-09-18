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


def btc_up_ok(btc, mode="strict"):
    # mode is user-tunable (config["btc_filter"]), per the user's 18 Sep
    # 2026 request that the BTC direction gate was blocking entries too
    # often (0 signals in a real 7-day window even at min_breaks=1):
    #   "strict" (default, original/live behavior): full EMA20>50>200 stack.
    #   "loose": only requires price above EMA50 -- directional but far
    #     less strict about a clean trend. Live since 18 Sep 2026.
    #   "very_loose": only requires price above EMA20 -- the shortest,
    #     most reactive average; closer to "price just turned up" than any
    #     real trend confirmation.
    #   "off": the BTC filter is bypassed entirely (always True) -- tested
    #     18 Sep 2026 and confirmed to break the walk-forward edge
    #     (NO_GO); NOT deployed.
    # The default keeps every already-deployed config's behavior
    # unchanged unless explicitly loosened.
    if mode == "off":
        return True
    if not btc or not btc.get("ema200"):
        return False
    if mode == "very_loose":
        return btc["c"] > btc["ema20"]
    if mode == "loose":
        return btc["c"] > btc["ema50"]
    return btc["c"] > btc["ema20"] > btc["ema50"] > btc["ema200"]


def btc_down_ok(btc, mode="strict"):
    if mode == "off":
        return True
    if not btc or not btc.get("ema200"):
        return False
    if mode == "very_loose":
        return btc["c"] < btc["ema20"]
    if mode == "loose":
        return btc["c"] < btc["ema50"]
    return btc["c"] < btc["ema20"] < btc["ema50"] < btc["ema200"]


def symmetric_features(rows, config):
    # donchian_windows (default WINDOWS=(10,20,40)) is user-tunable, per
    # the user's 18 Sep 2026 request for even more signal frequency after
    # min_breaks/btc_filter were already loosened as far as they safely
    # could go (btc_filter="off" was tested and confirmed to break the
    # walk-forward edge). Shortening the lookback makes a "breakout"
    # register more often without touching the BTC direction filter.
    windows = tuple(config.get("donchian_windows", WINDOWS))
    out = features(rows, config)
    for i, row in enumerate(out):
        up = down = 0
        for period in windows:
            if i >= period:
                window = rows[i - period:i]
                if row["c"] > max(x["h"] for x in window):
                    up += 1
                if row["c"] < min(x["l"] for x in window):
                    down += 1
        row["breaks_up"], row["breaks_down"] = up, down
        mid = windows[1]
        row["long_exit"] = min(x["l"] for x in rows[i - mid:i]) if i >= mid else None
        row["short_exit"] = max(x["h"] for x in rows[i - mid:i]) if i >= mid else None
    return out


def long_entry(row, btc, config):
    # min_breaks (default 2, of the 3 windows in WINDOWS) is user-tunable:
    # lowering it to 1 trades signal quality for frequency, per the user's
    # explicit 18 Sep 2026 request that the default (2) was too rare to be
    # a usable, active system -- see v5-loosened-frequency.yml for the
    # walk-forward/frequency comparison that justified the live value.
    if not btc_up_ok(btc, config.get("btc_filter", "strict")) or not row.get("atr") \
       or row.get("breaks_up", 0) < config.get("min_breaks", 2):
        return None
    stop = row["c"] - config["atr_multiplier"] * row["atr"]
    if stop <= 0 or 2 * (row["c"] - stop) / row["c"] < COST_HURDLE:
        return None
    return stop


def short_entry(row, btc, config):
    if not btc_down_ok(btc, config.get("btc_filter", "strict")) or not row.get("atr") \
       or row.get("breaks_down", 0) < config.get("min_breaks", 2):
        return None
    stop = row["c"] + config["atr_multiplier"] * row["atr"]
    if 2 * (stop - row["c"]) / row["c"] < COST_HURDLE:
        return None
    return stop


def long_stop(f, config):
    return f["c"] - config["atr_multiplier"] * f["atr"]


class ShortWindowLongModel:
    """Long-only, live-deployable version of the v5 signal (Part 1 of the
    long+short roadmap, 17 Sep 2026): same shorter 10/20/40-bar Donchian
    lookback tested in run_symmetric/evaluate, but shaped as a
    features/buy/sell/stop model so it plugs directly into the existing,
    already-validated crypto_v1.backtest.Engine and the live Spot
    pipeline (github_worker.py, render_web.py) that DonchianModel (V2)
    already uses -- no new exchange integration needed for the long side.

    IMPORTANT: the walk-forward test that passed (5/5 windows GO) used
    run_symmetric(), which has NO take-profit cap -- winners run until
    long_exit fires. Engine caps at 2R by default (cap_at_target=True);
    deploying this model with the default cap would NOT match what was
    actually tested (one window's result depended heavily on a single
    +26R trade that a 2R cap would have cut short). The live config MUST
    set "cap_at_target": false to faithfully match the tested behaviour.

    The short side (Part 2 of the roadmap) is NOT in this model -- it
    needs Binance Margin/Futures infrastructure that does not exist yet.

    (Fixed 18 Sep 2026: .sell now receives config as a 3rd argument --
    backtest.Engine.step passes it -- specifically so entry and exit use
    the SAME config["btc_filter"] mode. Before this fix, .sell always
    checked the strict BTC condition regardless of what mode opened the
    position; a position opened under a loosened entry filter would get
    force-exited one bar later by the mismatched strict exit check,
    producing a fake, suspiciously high win rate instead of a real
    result -- caught in run_symmetric before this model was also
    affected.)
    """
    @staticmethod
    def features(rows, config):
        return symmetric_features(rows, config)

    @staticmethod
    def buy(row, btc, config):
        return long_entry(row, btc, config) is not None

    @staticmethod
    def sell(row, btc, config=None):
        # config is now threaded through (backtest.Engine.step passes it as
        # of 18 Sep 2026, fixing the exact entry/exit btc_filter mismatch
        # documented above and caught in run_symmetric the same day) so a
        # loosened entry mode is honored on exit too, instead of silently
        # falling back to "strict" and force-exiting a real position one
        # bar after it opens.
        return long_exit(row, btc, (config or {}).get("btc_filter", "strict"))

    stop = staticmethod(long_stop)


def long_exit(row, btc, mode="strict"):
    # mode MUST match whatever mode opened the position (see btc_up_ok):
    # a position opened under a loosened entry filter but checked against
    # the strict filter on exit would almost always fail the strict check
    # on the very next bar, forcing a near-immediate exit regardless of
    # the real trade thesis -- caught via a suspicious 28/28-win, all-
    # trend_exit, sub-1R result when testing btc_filter="loose" (18 Sep
    # 2026); real trades were never given a chance to develop.
    return not btc_up_ok(btc, mode) or row.get("long_exit") is None or row["c"] < row["long_exit"]


def short_exit(row, btc, mode="strict"):
    return not btc_down_ok(btc, mode) or row.get("short_exit") is None or row["c"] > row["short_exit"]


def run_symmetric(data, symbols, c, start, end, funding=None, interval=FOUR_HOUR):
    prepared = {s: {r["t"]: r for r in symmetric_features(rows, c)} for s, rows in data.items()}
    times = sorted(t for t in prepared.get("BTCUSDT", {}) if start <= t < end)
    cash = c["initial_cash"]
    fee, slippage = c["fee"], c["slippage"]
    positions = {}
    pending = {}
    trades, curve = [], []
    marks = {}
    # Real perpetual funding events (every 8h on Binance USD-M Futures; a
    # BTCUSDT-wide proxy rate applied to every held position, long or
    # short) -- unmodeled before this was added, per the module docstring's
    # warning that short P&L here excludes borrow/funding cost.
    funding_events = sorted(funding or [], key=lambda e: e["t"])
    funding_index = 0

    def equity_now():
        # Full mark value (like backtest.Engine.equity()), NOT a delta from
        # entry -- that distinction matters: cash below is debited/credited
        # the FULL position cost/proceeds at open, so equity must add back
        # the FULL current mark value, signed by direction, to stay correct.
        return cash + sum(
            p["qty"] * marks.get(s, p["entry"]) * (1 if p["side"] == "long" else -1)
            for s, p in positions.items()
        )

    def close(symbol, price, t):
        nonlocal cash
        p = positions.pop(symbol)
        if p["side"] == "long":
            fill = price * (1 - slippage)
            received = p["qty"] * fill * (1 - fee)
            pnl = received - p["qty"] * p["entry"] * (1 + fee)
            cash += received  # mirrors the full cost debited at open
        else:
            fill = price * (1 + slippage)
            cost = p["qty"] * fill * (1 + fee)
            pnl = p["qty"] * p["entry"] * (1 - fee) - cost
            cash -= cost  # mirrors the full proceeds credited at open
        return fill, pnl, p

    for t in times:
        bars = {s: prepared[s][t] for s in prepared if t in prepared[s]}
        marks.update({s: f["o"] for s, f in bars.items()})
        # Longs pay shorts when rate>0 (the normal, bullish-market case);
        # shorts pay longs when rate<0. Charged on whatever is held at the
        # moment the event fires, at that position's current mark price.
        while funding_index < len(funding_events) and funding_events[funding_index]["t"] <= t:
            rate = funding_events[funding_index]["rate"]
            for symbol, p in positions.items():
                notional = p["qty"] * marks.get(symbol, p["entry"])
                cash += -notional * rate if p["side"] == "long" else notional * rate
            funding_index += 1
        for symbol, side in list(pending.items()):
            if symbol in positions or symbol not in bars:
                continue
            if len(positions) >= c["max_positions"]:
                continue
            f = bars[symbol]
            entry = f["o"] * (1 + slippage) if side == "long" else f["o"] * (1 - slippage)
            stop = long_entry(f, bars.get("BTCUSDT"), c) if side == "long" else short_entry(f, bars.get("BTCUSDT"), c)
            if stop is None:
                continue
            unit_risk = abs(entry - stop)
            if unit_risk <= 0:
                continue
            # Real risk-based sizing (mirrors risk.size_position): a FIXED
            # dollar risk (fraction of equity) divided by the ACTUAL stop
            # distance -- not an assumed distance. The first version here
            # assumed a flat 2% stop for sizing regardless of the real ATR
            # stop, AND only debited/credited the entry fee (not the full
            # notional) while the close side moved the full amount -- a
            # double-count that compounded into an exponential blow-up
            # (caught in the first real run: >20,000% window returns, not
            # a real result). Both are fixed now: real unit_risk, and cash
            # moves the FULL notional symmetrically at open and close.
            risk_usdt = equity_now() * c["risk_fraction"]
            qty = min(risk_usdt / unit_risk, cash * 0.9 / entry) if entry else 0
            if qty * entry >= 10:
                cash = cash - qty * entry * (1 + fee) if side == "long" else cash + qty * entry * (1 - fee)
                positions[symbol] = dict(side=side, qty=qty, entry=entry, stop=stop, entry_time=t)
        pending = {}
        for symbol, p in list(positions.items()):
            if symbol not in bars:
                continue
            f = bars[symbol]
            hit_stop = (f["l"] <= p["stop"]) if p["side"] == "long" else (f["h"] >= p["stop"])
            if hit_stop:
                fill, pnl, pos = close(symbol, p["stop"], t)
                trades.append(dict(symbol=symbol, side=pos["side"], entry_time=pos["entry_time"], exit_time=t,
                                    entry=pos["entry"], exit=fill, qty=pos["qty"], pnl=pnl,
                                    risk=abs(pos["entry"] - pos["stop"]) * pos["qty"],
                                    r_multiple=pnl / (abs(pos["entry"] - pos["stop"]) * pos["qty"] or 1), reason="stop"))
        for symbol, p in list(positions.items()):
            if symbol not in bars:
                continue
            f = bars[symbol]
            btc_mode = c.get("btc_filter", "strict")
            exited = (long_exit(f, bars.get("BTCUSDT"), btc_mode) if p["side"] == "long"
                     else short_exit(f, bars.get("BTCUSDT"), btc_mode))
            if exited:
                fill, pnl, pos = close(symbol, f["c"], t)
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
        curve.append(dict(time=t + interval, equity=equity_now(), positions=len(positions)))
    return dict(trades=trades, curve=curve, positions={})


def evaluate(data, symbols, c, manifest, count=5, min_trades=8, warm=100, funding=None, interval=FOUR_HOUR):
    """data: native 15m rows (as loaded by crypto_v1.data.load) -- aggregated
    to `interval` internally (default 4h), so the same cached 15m dataset
    used by every other variant here can be reused without a new fetch.

    funding: optional real BTCUSDT-proxy funding events (see run_symmetric)
    applied to every window's normal and stress runs alike, so a real
    leveraged-perpetual cost/benefit is reflected in the GO/NO_GO verdict
    rather than only unleveraged spot-style fee/slippage."""
    agg_data = {symbol: aggregate(rows, interval) for symbol, rows in data.items()}
    symbols = [s for s in symbols if len(agg_data.get(s, [])) >= 200]
    times = [r["t"] for r in agg_data["BTCUSDT"] if manifest["start"] <= r["t"] < manifest["end"]]
    min_bars = warm + count * min_trades * 4
    if len(times) < min_bars:
        raise ValueError(f"At least {min_bars} BTC candles required")
    starts = wf.windows_from_times(times, count, warm=warm, min_trades=min_trades) + [manifest["end"]]
    windows = []
    for i in range(count):
        start, end = starts[i], starts[i + 1]
        normal = metrics(run_symmetric(agg_data, symbols, c, start, end, funding, interval), c)
        cs = wf.stress_config(c)
        stress = metrics(run_symmetric(agg_data, symbols, cs, start, end, funding, interval), cs)
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
