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
from datetime import datetime, timezone

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


def ichimoku_strong(rows, params=(9, 26, 52)):
    """Per bar: is the coin's own Ichimoku fully bullish? Price above the
    cloud drawn under this bar, Tenkan above Kijun, the cloud being drawn
    ahead green, and Chikou (this close) above the close `kijun` bars back.
    None until there is enough history. Uses bars up to i only.

    The user's favourite indicator (24 Sep 2026). As an extra condition on
    every long, over 3 years it took 100 USDT (4H+2H) to 1766 instead of
    682, drawdown 40% -> 34%, with 15.3 instead of 19.3 trades a week; both
    systems and every half-year improved or held, and 7/22/44 and 12/30/60
    gave 1150-1394, so it is not a knife-edge on the standard settings."""
    tenkan_n, kijun_n, span_b_n = (int(x) for x in params)
    highs, lows, closes = [r["h"] for r in rows], [r["l"] for r in rows], [r["c"] for r in rows]

    def midline(n):
        return [(max(highs[i - n + 1:i + 1]) + min(lows[i - n + 1:i + 1])) / 2 if i >= n - 1 else None
                for i in range(len(rows))]

    tenkan, kijun, span_b = midline(tenkan_n), midline(kijun_n), midline(span_b_n)
    span_a = [(t + k) / 2 if t is not None and k is not None else None for t, k in zip(tenkan, kijun)]
    out = [None] * len(rows)
    for i in range(span_b_n - 1 + kijun_n, len(rows)):
        cloud_a, cloud_b = span_a[i - kijun_n], span_b[i - kijun_n]  # plotted kijun bars ahead
        out[i] = (closes[i] > max(cloud_a, cloud_b) and tenkan[i] > kijun[i]
                  and span_a[i] > span_b[i] and closes[i] > closes[i - kijun_n])
    return out


def symmetric_features(rows, config):
    # donchian_windows (default WINDOWS=(10,20,40)) is user-tunable, per
    # the user's 18 Sep 2026 request for even more signal frequency after
    # min_breaks/btc_filter were already loosened as far as they safely
    # could go (btc_filter="off" was tested and confirmed to break the
    # walk-forward edge). Shortening the lookback makes a "breakout"
    # register more often without touching the BTC direction filter.
    windows = tuple(config.get("donchian_windows", WINDOWS))
    out = features(rows, config)
    # regime_ma_days: a simple moving average over that many DAYS of closes
    # (bar count derived from the rows' own spacing, so 2h and 4h agree),
    # read off BTC's row by long_entry as the market-regime line.
    regime_days = config.get("regime_ma_days")
    if regime_days and len(rows) > 1:
        period = max(1, int(regime_days * 86_400_000 // (rows[1]["t"] - rows[0]["t"])))
        running = 0.0
        for i, row in enumerate(out):
            running += rows[i]["c"]
            if i >= period:
                running -= rows[i - period]["c"]
            row["regime_ma"] = running / period if i + 1 >= period else None
    ichimoku = ichimoku_strong(rows, config.get("ichimoku_params", (9, 26, 52))) \
        if config.get("ichimoku_filter") else None
    # btc_exit_bars (24 Sep 2026): how many consecutive closes BTC must spend
    # outside the BTC filter before the long side is closed. Counted on every
    # series; only BTC's own row is ever read (see long_exit).
    exit_bars = int(config.get("btc_exit_bars", 1) or 1)
    btc_mode = config.get("btc_filter", "strict")
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
        if exit_bars > 1:
            weak = not btc_up_ok(row, btc_mode)
            row["btc_weak_run"] = (out[i - 1]["btc_weak_run"] + 1 if i else 1) if weak else 0
        row["short_exit"] = max(x["h"] for x in rows[i - mid:i]) if i >= mid else None
        # Context for the "do not buy a first-time vertical move" filters in
        # long_entry (23 Sep 2026, user's observation on NIL/SAGA). Computed
        # only when a filter actually asks for it -- each is a window scan
        # over every bar of every symbol.
        longest = windows[-1]
        if config.get("max_extension_atr"):
            row["break_level"] = max(x["h"] for x in rows[i - longest:i]) if i >= longest else None
        if config.get("max_runup_fraction"):
            span = int(config.get("runup_bars", 10))
            row["runup"] = (row["c"] / rows[i - span]["c"] - 1) if i >= span and rows[i - span]["c"] else None
        if config.get("skip_first_time_high"):
            span = int(config.get("first_time_high_bars", 200))
            row["range_high"] = max(x["h"] for x in rows[i - span:i]) if i >= span else None
            row["has_history"] = i >= span
        if ichimoku is not None:
            row["ichimoku_ok"] = ichimoku[i]
        if config.get("max_week_gain") is not None and len(rows) > 1:
            week = 7 * max(1, int(86_400_000 // (rows[1]["t"] - rows[0]["t"])))
            row["week_gain"] = row["c"] / rows[i - week]["c"] - 1 if i >= week and rows[i - week]["c"] else None
        if "momentum" in str(config.get("entry_mode", "")) and len(rows) > 1:
            # Pre-move context for is_momentum_setup, in calendar time so 2h
            # and 4h bars measure the same thing: the last 48h's range and
            # the last 24h's return.
            per_day = max(1, int(86_400_000 // (rows[1]["t"] - rows[0]["t"])))
            if i >= 2 * per_day:
                recent = rows[i - 2 * per_day + 1:i + 1]
                row["range48"] = (max(x["h"] for x in recent) - min(x["l"] for x in recent)) / row["c"]
                row["ret24"] = row["c"] / rows[i - per_day]["c"] - 1
    return out


def is_trend_pullback(row, config):
    """Second entry pattern (23 Sep 2026, user's request for activity in the
    quiet hours a breakout system sits out): a coin already in an uptrend
    (EMA20 above EMA50, close above EMA50) whose bar dipped to its EMA20 and
    closed back above it -- buying the dip inside a trend rather than the
    new high. pullback_touch_atr lets the low stop short of the EMA by that
    many ATRs and still count as a touch."""
    ema20, ema50, atr = row.get("ema20"), row.get("ema50"), row.get("atr")
    if not ema20 or not ema50 or not atr:
        return False
    touch = ema20 + float(config.get("pullback_touch_atr", 0)) * atr
    return ema20 > ema50 and row["c"] > ema50 and row["l"] <= touch and row["c"] > ema20


def is_momentum_setup(row, config):
    """Third entry pattern (23 Sep 2026, the user's pre-pump study): what
    preceded >=20% daily rises in three years of data was NOT a quiet
    squeeze (that halved the odds) but a coin already moving -- a wide 48h
    range, a positive last 24h, above EMA50 above EMA200. Those bars were
    followed by a >=20% rise ~5-10x as often as a random bar, holding in
    both the fit period and the held-out last year."""
    if row.get("range48") is None or not row.get("ema50") or not row.get("ema200"):
        return False
    return (row["range48"] >= float(config.get("momentum_min_range48", 0.154))
            and row["ret24"] >= float(config.get("momentum_min_ret24", 0.033))
            and row["c"] > row["ema50"] > row["ema200"])


def long_entry(row, btc, config):
    # min_breaks (default 2, of the 3 windows in WINDOWS) is user-tunable:
    # lowering it to 1 trades signal quality for frequency, per the user's
    # explicit 18 Sep 2026 request that the default (2) was too rare to be
    # a usable, active system -- see v5-loosened-frequency.yml for the
    # walk-forward/frequency comparison that justified the live value.
    # entry_mode: "breakout" (default, the validated live signal),
    # "pullback" (is_trend_pullback only) or "both".
    # "momentum" (is_momentum_setup) and "breakout+momentum" join them.
    mode = config.get("entry_mode", "breakout")
    breakout = row.get("breaks_up", 0) >= config.get("min_breaks", 2)
    if mode == "breakout":
        fired = breakout
    elif mode == "pullback":
        fired = is_trend_pullback(row, config)
    elif mode == "both":
        fired = breakout or is_trend_pullback(row, config)
    elif mode == "momentum":
        fired = is_momentum_setup(row, config)
    elif mode == "breakout+momentum":
        fired = breakout or is_momentum_setup(row, config)
    else:
        raise ValueError(f"unknown entry_mode {mode!r}")
    if not btc_up_ok(btc, config.get("btc_filter", "strict")) or not row.get("atr") or not fired:
        return None
    # Time-of-week filter (23 Sep 2026, research option from the user's
    # Gemini notes): thin weekend / off-hours liquidity is easier to push
    # around. Keyed on the signal bar's own UTC open time, off by default.
    if config.get("skip_weekend_entries") or config.get("skip_entry_hours_utc"):
        opened = datetime.fromtimestamp(row["t"] / 1000, timezone.utc)
        if config.get("skip_weekend_entries") and opened.weekday() >= 5:
            return None
        if opened.hour in set(config.get("skip_entry_hours_utc") or ()):
            return None
    # Market regime (23 Sep 2026): every losing walk-forward window was one
    # where BTC fell 20%+ and spent most of its time under its long average.
    # No new longs while BTC is below its regime_ma_days average. Unknown
    # (not enough history yet) blocks too: fail closed, never open blind.
    if config.get("regime_ma_days"):
        if not btc.get("regime_ma") or btc["c"] < btc["regime_ma"]:
            return None
    # max_week_gain (24 Sep 2026, the user's reading of NIL's +210% week):
    # no new long in a coin that already rose more than this over the last
    # 7 days. Over 3 years on 4h bars, entries after a 50%+ week lost on
    # average; any cap between 40% and 75% improved the 4H result ~11% at a
    # cost of 0.2 trades a week. On 2h bars it hurt, so only config_v5_long
    # sets it. Unknown (too little history) blocks: fail closed.
    week_cap = config.get("max_week_gain")
    if week_cap is not None:
        if row.get("week_gain") is None or row["week_gain"] > float(week_cap):
            return None
    # ichimoku_filter (24 Sep 2026): only buy while the coin's own Ichimoku
    # is fully bullish (see ichimoku_strong). Unknown blocks: fail closed.
    if config.get("ichimoku_filter") and row.get("ichimoku_ok") is not True:
        return None
    # Optional "quality of the breakout" filters, all off by default so the
    # live signal is unchanged until one is validated. They exist because a
    # Donchian break says only THAT a high was taken out, never how violent
    # the move getting there was -- live 23 Sep 2026: NIL and SAGA both
    # qualified on a first-ever vertical run and went straight into loss.
    extension = config.get("max_extension_atr")
    if extension and row.get("break_level") is not None:
        # How far ABOVE the level it broke are we already buying?
        if row["c"] - row["break_level"] > float(extension) * row["atr"]:
            return None
    runup = config.get("max_runup_fraction")
    if runup and row.get("runup") is not None and row["runup"] > float(runup):
        return None
    if config.get("skip_first_time_high") and row.get("range_high") is not None:
        # Never traded this high before in the lookback: no earlier holder
        # has had a chance to take profit, and every one of them is now in
        # front of us.
        if row["c"] > row["range_high"]:
            return None
    # Confirmation filters, all off by default (23 Sep 2026): a Donchian
    # break says a high was taken out, not whether volume, momentum or the
    # coin's own trend agree. Each is measured separately before any is
    # deployed -- see the walk-forward comparison in ARASTIRMA.md.
    volume_multiple = config.get("entry_volume_multiple")
    if volume_multiple and row.get("volume_avg"):
        if row["v"] < float(volume_multiple) * row["volume_avg"]:
            return None
    rsi_min, rsi_max = config.get("entry_rsi_min"), config.get("entry_rsi_max")
    if (rsi_min or rsi_max) and row.get("rsi") is not None:
        if rsi_min and row["rsi"] < float(rsi_min):
            return None
        if rsi_max and row["rsi"] > float(rsi_max):
            return None
    if config.get("require_coin_trend"):
        ema20, ema50 = row.get("ema20"), row.get("ema50")
        if not ema20 or not ema50 or ema20 <= ema50 or row["c"] <= ema20:
            return None
    stop = row["c"] - config["atr_multiplier"] * row["atr"]
    if stop <= 0 or 2 * (row["c"] - stop) / row["c"] < COST_HURDLE:
        return None
    return stop


def short_entry(row, btc, config):
    if not btc_down_ok(btc, config.get("btc_filter", "strict")) or not row.get("atr") \
       or row.get("breaks_down", 0) < config.get("min_breaks", 2):
        return None
    # disable_shorts: research switch to measure the long side alone inside
    # run_symmetric. short_regime_only: the mirror of the long regime line --
    # shorts only while BTC is BELOW its regime_ma_days average, so the two
    # sides take turns instead of fighting each other. Unknown line blocks.
    if config.get("disable_shorts"):
        return None
    if config.get("short_regime_only") and config.get("regime_ma_days"):
        if not btc.get("regime_ma") or btc["c"] >= btc["regime_ma"]:
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
        config = config or {}
        return long_exit(row, btc, config.get("btc_filter", "strict"), config.get("btc_exit_bars", 1))

    stop = staticmethod(long_stop)


def long_exit(row, btc, mode="strict", btc_bars=1):
    # mode MUST match whatever mode opened the position (see btc_up_ok):
    # a position opened under a loosened entry filter but checked against
    # the strict filter on exit would almost always fail the strict check
    # on the very next bar, forcing a near-immediate exit regardless of
    # the real trade thesis -- caught via a suspicious 28/28-win, all-
    # trend_exit, sub-1R result when testing btc_filter="loose" (18 Sep
    # 2026); real trades were never given a chance to develop.
    # btc_bars > 1 (24 Sep 2026): BTC must have closed outside the filter
    # that many bars in a row. With one bar, a 2H system sold every position
    # each time BTC dipped under its 2h EMA50 for a single candle and bought
    # back two hours later (ACE/LTC/ZRO, 24 Sep); over 3 years two bars took
    # the 2H+4H result from 1766 to 3376 (better in 5 of 6 half-years, 3 bars
    # 2486), drawdown 34% -> 39%. A BTC row without the run count (the
    # setting off when it was built) falls back to the single-bar check.
    if btc_bars and int(btc_bars) > 1 and btc is not None and "btc_weak_run" in btc:
        btc_failed = btc["btc_weak_run"] >= int(btc_bars)
    else:
        btc_failed = not btc_up_ok(btc, mode)
    return btc_failed or row.get("long_exit") is None or row["c"] < row["long_exit"]


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
        for symbol, (side, stop) in list(pending.items()):
            if symbol in positions or symbol not in bars:
                continue
            if len(positions) >= c["max_positions"]:
                continue
            f = bars[symbol]
            entry = f["o"] * (1 + slippage) if side == "long" else f["o"] * (1 - slippage)
            # The stop comes from the SIGNAL bar, fixed when the order was
            # queued. Until 23 Sep 2026 this re-ran long_entry/short_entry
            # on the entry bar itself -- whose close, ATR and breakout count
            # are not known yet at its open -- so a trade was only "taken"
            # if the bar it opened on went on to close as a breakout too.
            # That lookahead silently dropped nearly every immediate failure
            # and inflated every run_symmetric result, including the 5/5 GO
            # the v5 deployment was based on.
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
            exited = (long_exit(f, bars.get("BTCUSDT"), btc_mode, c.get("btc_exit_bars", 1)) if p["side"] == "long"
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
            long_stop_level = long_entry(f, bars.get("BTCUSDT"), c)
            if long_stop_level is not None:
                pending[symbol] = ("long", long_stop_level)
                continue
            short_stop_level = short_entry(f, bars.get("BTCUSDT"), c)
            if short_stop_level is not None:
                pending[symbol] = ("short", short_stop_level)
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
