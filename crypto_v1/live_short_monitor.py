"""Automatic trend and emergency exits for one protected leveraged short
position. Mirrors live_monitor.py's exit_decision/execute_exit exactly,
direction-flipped: profit is price falling, risk is price rising past the
exchange-native protective stop (already resting on Binance regardless of
this loop, same as the long side's stop-loss order)."""
from decimal import Decimal
from .binance_futures import OrderStateUnknown
from .live_controller import _known_or_place
from .research_v5 import short_exit as short_trend_exit


def short_exit_decision(position, feature, btc, low, config, sell_fn=None):
    entry = Decimal(str(position["entry"]))
    stop = Decimal(str(position["stop_price"]))
    current = Decimal(str(feature["c"]))
    risk = stop - entry
    if risk <= 0:
        return "emergency_risk"
    sell_fn = sell_fn or (lambda f, b: short_trend_exit(f, b))
    if sell_fn(feature, btc):
        return "profit_signal" if current < entry else "emergency_risk"
    if Decimal(str(low)) <= entry - risk:
        trailing = Decimal(str(low)) + Decimal(str(config["trailing_atr"])) * Decimal(str(feature["atr"]))
        if current >= trailing:
            return "trailing_profit" if current < entry else "emergency_risk"
    return None


def execute_short_exit(position, reason, executor):
    symbol, stop_id = position["symbol"], position["stop_client_id"]
    try:
        canceled = executor.cancel(symbol, stop_id)
    except OrderStateUnknown:
        canceled = executor.query(symbol, stop_id)
    status = canceled.get("status")
    if status == "FILLED":
        return {"status": "already_stopped", "reason": reason}
    if status not in {"CANCELED", "EXPIRED"}:
        raise RuntimeError("protective stop cancellation is unconfirmed")
    exit_id = "kv1fx" + stop_id.removeprefix("kv1fp")
    closed = _known_or_place(executor, symbol, exit_id,
                             lambda: executor.market_close_short(symbol, position["quantity"], exit_id))
    return {"status": "closed", "reason": reason, "order": closed}
