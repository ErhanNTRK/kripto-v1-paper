"""Orchestrate one Telegram-approved short and its exchange-native
protection on Binance Futures. Mirrors live_controller.approve_buy, with
two leverage-specific additions: leverage/isolated-margin are set before
opening, and the REAL liquidation price Binance reports after opening is
checked against the protective stop before that stop is placed -- an
unsafe combination (stop too close to liquidation, e.g. from unexpected
slippage or fee eating into the margin buffer) triggers an immediate
market close instead of ever resting a stop that liquidation could beat."""
from decimal import Decimal

from .binance_trade import OrderRejected, OrderStateUnknown
from .live_controller import _known_or_place
from .live_execution import execution_enabled, leveraged_order_plan, liquidation_is_safe
from .live_limits import confirmed_signal, may_open
from .live_signal import pending_short_candidates

LIQUIDATION_SAFETY_BUFFER = 0.2  # stop must sit >=20% of the liquidation distance away


def _position_for(symbol, position_risk):
    for entry in position_risk:
        if entry.get("symbol") == symbol and abs(float(entry.get("positionAmt", "0"))) > 0:
            return entry
    return None


def approve_short(update_id, command, now_ms, saved, config, environment, market, executor):
    candidates = pending_short_candidates(saved, now_ms, config)
    signal, reason = confirmed_signal(command, candidates, now_ms, config)
    if not signal:
        return {"status": "rejected", "reason": reason}
    status = market.pilot_status()
    allowed, reason = may_open(dict(config, live_trading_enabled=True),
                               status["open_positions"], status["opens_today"],
                               status["realized_loss_today"], status["pilot_drawdown"])
    if not allowed:
        return {"status": "rejected", "reason": reason}
    price = Decimal(str(market.price(signal["symbol"])))
    close = Decimal(str(signal["close"]))
    stop = Decimal(str(signal["stop"]))
    drift = Decimal(str(config.get("max_entry_drift_fraction", 0.005)))
    if price >= stop or price < close * (Decimal("1") - drift):
        return {"status": "rejected", "reason": "entry_price_moved"}
    rules = market.rules(signal["symbol"])
    plan = leveraged_order_plan("short", price, stop, status["free_usdt"],
                                config["risk_per_trade_usdt"], signal["leverage"], rules)
    plan.update(symbol=signal["symbol"], entry_price=format(price, "f"))
    if not execution_enabled(config, environment):
        return {"status": "preview", "reason": "real_orders_disabled", "plan": plan}
    symbol = signal["symbol"]
    executor.set_isolated_margin(symbol)
    executor.set_leverage(symbol, plan["leverage"])
    open_id = f"kv1fs{int(update_id)}"
    opened = _known_or_place(executor, symbol, open_id,
                             lambda: executor.market_open_short(symbol, plan["quantity"], open_id))
    filled_qty = Decimal(str(opened.get("executedQty", "0")))
    if opened.get("status") != "FILLED" or filled_qty <= 0:
        raise RuntimeError("short open was not fully filled; operator review required")
    position = _position_for(symbol, executor.position_risk(symbol))
    liquidation_price = position.get("liquidationPrice") if position else "0"
    if not liquidation_is_safe("short", plan["stop_price"], liquidation_price, LIQUIDATION_SAFETY_BUFFER):
        closed = _known_or_place(executor, symbol, f"kv1fe{int(update_id)}",
                                 lambda: executor.market_close_short(symbol, format(filled_qty, "f"),
                                                                     f"kv1fe{int(update_id)}"))
        return {"status": "opened_then_emergency_closed", "open_order": opened,
                "emergency_close": closed, "reason": "liquidation_too_close",
                "liquidation_price": liquidation_price, "plan": plan}
    stop_id = f"kv1fp{int(update_id)}"
    try:
        stop_order = _known_or_place(
            executor, symbol, stop_id,
            lambda: executor.protective_stop_for_short(symbol, format(filled_qty, "f"),
                                                        plan["stop_price"], stop_id))
    except OrderRejected:
        closed = _known_or_place(executor, symbol, f"kv1fe{int(update_id)}",
                                 lambda: executor.market_close_short(symbol, format(filled_qty, "f"),
                                                                     f"kv1fe{int(update_id)}"))
        return {"status": "opened_then_emergency_closed", "open_order": opened,
                "emergency_close": closed, "reason": "protective_stop_rejected", "plan": plan}
    return {"status": "opened_and_protected", "open_order": opened,
            "stop_order": stop_order, "liquidation_price": liquidation_price, "plan": plan}
