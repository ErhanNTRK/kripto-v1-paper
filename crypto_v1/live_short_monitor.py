"""Automatic trend and emergency exits for one protected leveraged short
position. Mirrors live_monitor.py's exit_decision/execute_exit exactly,
direction-flipped: profit is price falling, risk is price rising past the
exchange-native protective stop (already resting on Binance regardless of
this loop, same as the long side's stop-loss order)."""
from decimal import Decimal
from .binance_trade import OrderRejected
from .live_controller import _known_or_place
from .research_v5 import short_exit as short_trend_exit


def short_exit_decision(position, feature, btc, low, config, sell_fn=None):
    """config["btc_filter"] (default "strict") MUST match whatever mode
    short_entry used to open this position -- mirrors the same fix applied
    to research_v5.run_symmetric and ShortWindowLongModel.sell on 18 Sep
    2026. A position opened under a loosened filter that gets checked
    against the strict default on exit is force-closed almost immediately,
    producing a fake result instead of a real one."""
    entry = Decimal(str(position["entry"]))
    stop = Decimal(str(position["stop_price"]))
    current = Decimal(str(feature["c"]))
    risk = stop - entry
    if risk <= 0:
        return "emergency_risk"
    sell_fn = sell_fn or (lambda f, b: short_trend_exit(f, b, config.get("btc_filter", "strict")))
    if sell_fn(feature, btc):
        return "profit_signal" if current < entry else "emergency_risk"
    if Decimal(str(low)) <= entry - risk:
        trailing = Decimal(str(low)) + Decimal(str(config["trailing_atr"])) * Decimal(str(feature["atr"]))
        if current >= trailing:
            return "trailing_profit" if current < entry else "emergency_risk"
    return None


def open_amount(executor, symbol, side):
    """How much of `side` is really open on the exchange right now (0 when
    nothing is, or when the position points the other way)."""
    for entry in executor.position_risk(symbol):
        if entry.get("symbol") != symbol:
            continue
        amount = Decimal(str(entry.get("positionAmt", "0")))
        if (side == "long" and amount > 0) or (side == "short" and amount < 0):
            return abs(amount)
    return Decimal("0")


def cancel_quietly(executor, symbol, stop_id):
    """Best-effort removal of a protective stop the position no longer needs.
    Every stop is reduce-only, so one left behind can never open or flip a
    position; the scan cancels any that remain once nothing is held."""
    try:
        executor.cancel(symbol, stop_id)
    except Exception as exc:
        print(f"Stop cancel after exit left for later ({symbol} {stop_id}): {exc}", flush=True)


def _close_then_cancel(position, reason, executor, side):
    """Close FIRST (a reduce-only market order for what is really open), then
    cancel the stop. Until 24 Sep 2026 the stop was cancelled first and the
    close was refused whenever that cancel could not be confirmed, which left
    ACE, ARB, SUPER and NIL open with no stop for 14-19 minutes. Reduce-only
    means this close can neither flip the position nor close it twice."""
    symbol, stop_id = position["symbol"], position["stop_client_id"]
    stop_prefix, exit_prefix = ("kv1fq", "kv1fy") if side == "long" else ("kv1fp", "kv1fx")
    close = executor.market_close_long if side == "long" else executor.market_close_short
    amount = open_amount(executor, symbol, side)
    if amount <= 0:
        # The stop (or a person) already closed it; only the stop is left.
        cancel_quietly(executor, symbol, stop_id)
        return {"status": "already_stopped", "reason": reason}
    exit_id = exit_prefix + stop_id.removeprefix(stop_prefix)
    try:
        closed = _known_or_place(executor, symbol, exit_id,
                                 lambda: close(symbol, format(amount, "f"), exit_id))
    except OrderRejected as error:
        if error.code != -2022:  # Binance: ReduceOnly order rejected -> nothing left to reduce.
            raise
        cancel_quietly(executor, symbol, stop_id)
        return {"status": "already_stopped", "reason": reason}
    cancel_quietly(executor, symbol, stop_id)
    return {"status": "closed", "reason": reason, "order": closed}


def execute_short_exit(position, reason, executor):
    return _close_then_cancel(position, reason, executor, "short")


def execute_long_futures_exit(position, reason, executor):
    """Mirrors execute_short_exit for a leveraged LONG futures position (21
    Sep 2026). The exit DECISION for a long is identical math to Spot's
    live_monitor.exit_decision (same "high since entry" trailing logic) --
    only the order placement differs by venue."""
    return _close_then_cancel(position, reason, executor, "long")
