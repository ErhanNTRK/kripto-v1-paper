"""Fail-closed planning primitives for the future Binance Spot executor.

This module does not contact Binance and cannot submit an order.
"""
from decimal import Decimal, ROUND_DOWN


LIVE_PHRASE = "ENABLE_KRIPTO_V1_REAL_ORDERS"


def execution_enabled(config, environment):
    """Require two independent switches so a deploy cannot enable trading by accident."""
    return (config.get("live_trading_enabled") is True
            and environment.get("LIVE_TRADING_CONFIRMATION") == LIVE_PHRASE)


def _down(value, step):
    value, step = Decimal(str(value)), Decimal(str(step))
    if value <= 0 or step <= 0:
        raise ValueError("positive value and step required")
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def symbol_rules(exchange_symbol):
    filters = {item["filterType"]: item for item in exchange_symbol["filters"]}
    price = filters["PRICE_FILTER"]
    lot = filters["LOT_SIZE"]
    notional = filters.get("NOTIONAL") or filters.get("MIN_NOTIONAL")
    if not notional:
        raise ValueError("notional filter missing")
    return {
        "tick_size": Decimal(price["tickSize"]),
        "step_size": Decimal(lot["stepSize"]),
        "min_qty": Decimal(lot["minQty"]),
        "min_notional": Decimal(notional["minNotional"]),
    }


def protective_order_plan(entry, stop, free_usdt, risk_usdt, rules):
    """Size a fully-funded position and precompute its native stop order."""
    entry = Decimal(str(entry))
    stop = Decimal(str(stop))
    free_usdt = Decimal(str(free_usdt))
    risk_usdt = Decimal(str(risk_usdt))
    if not (entry > stop > 0 and free_usdt > 0 and risk_usdt > 0):
        raise ValueError("invalid live order inputs")
    raw_qty = min(risk_usdt / (entry - stop), free_usdt / entry)
    qty = _down(raw_qty, rules["step_size"])
    stop_price = _down(stop, rules["tick_size"])
    # A sell stop-limit sits one tick below its trigger to improve execution odds.
    limit_price = stop_price - rules["tick_size"]
    if qty < rules["min_qty"] or qty * entry < rules["min_notional"]:
        raise ValueError("order is below Binance minimums")
    if limit_price <= 0:
        raise ValueError("invalid protective limit price")
    planned_loss = qty * (entry - stop_price)
    if planned_loss > risk_usdt:
        raise ValueError("rounded plan exceeds risk budget")
    return {
        "quantity": format(qty, "f"),
        "stop_price": format(stop_price, "f"),
        "stop_limit_price": format(limit_price, "f"),
        "planned_loss_usdt": format(planned_loss, "f"),
    }
