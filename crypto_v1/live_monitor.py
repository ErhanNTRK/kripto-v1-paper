"""Automatic target, trailing, trend and emergency exits for one protected position."""
from decimal import Decimal
from .binance_trade import OrderStateUnknown
from .live_controller import _known_or_place
from .strategy import sell_signal


def exit_decision(position, feature, btc, high, config):
    entry = Decimal(str(position["entry"]))
    stop = Decimal(str(position["stop_price"]))
    current = Decimal(str(feature["c"]))
    risk = entry - stop
    if risk <= 0: return "emergency_risk"
    target = entry + Decimal(str(config["minimum_reward_risk"])) * risk
    if current >= target: return "take_profit"
    if sell_signal(feature, btc):
        return "profit_signal" if current > entry else "emergency_risk"
    if Decimal(str(high)) >= entry + risk:
        trailing = Decimal(str(high)) - Decimal(str(config["trailing_atr"])) * Decimal(str(feature["atr"]))
        if current <= trailing:
            return "trailing_profit" if current > entry else "emergency_risk"
    return None


def execute_exit(position, reason, executor):
    symbol, stop_id = position["symbol"], position["stop_client_id"]
    try:
        canceled = executor.cancel(symbol, stop_id)
    except OrderStateUnknown:
        canceled = executor.query(symbol, stop_id)
    status = canceled.get("status")
    if status == "FILLED": return {"status": "already_stopped", "reason": reason}
    if status not in {"CANCELED", "EXPIRED"}:
        raise RuntimeError("protective stop cancellation is unconfirmed")
    exit_id = "kv1x" + stop_id.removeprefix("kv1s")
    sold = _known_or_place(executor, symbol, exit_id,
                           lambda: executor.market_sell(symbol, position["quantity"], exit_id))
    return {"status": "sold", "reason": reason, "order": sold}
