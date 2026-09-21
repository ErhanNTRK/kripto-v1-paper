"""Fail-closed planning primitives for the Binance Spot and Futures executors.

This module does not contact Binance and cannot submit an order.
"""
from decimal import Decimal, ROUND_DOWN, ROUND_UP


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


def _up(value, step):
    value, step = Decimal(str(value)), Decimal(str(step))
    if value <= 0 or step <= 0:
        raise ValueError("positive value and step required")
    return (value / step).to_integral_value(rounding=ROUND_UP) * step


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
        # Max decimals Binance accepts for quoteOrderQty (8 for every USDT
        # pair today; read from exchangeInfo rather than assumed).
        "quote_precision": int(exchange_symbol.get("quoteAssetPrecision", 8)),
    }


def quote_amount(quantity, price, rules):
    """quoteOrderQty for a MARKET buy of `quantity` at `price`, rounded DOWN
    to the symbol's quote precision. Live-observed 21 Sep 2026: quantity
    (step-rounded, 8 decimals as Binance formats stepSize) times lastPrice
    (8 decimals as Binance formats it) is a 16-decimal Decimal, and sending
    it verbatim got every single auto-entry rejected with -1111
    BAD_PRECISION -- the first real buy attempts the system ever made."""
    precision = int(rules.get("quote_precision", 8))
    return format(_down(Decimal(str(quantity)) * Decimal(str(price)),
                        Decimal(1).scaleb(-precision)), "f")


def futures_symbol_rules(exchange_symbol):
    filters = {item["filterType"]: item for item in exchange_symbol["filters"]}
    price = filters["PRICE_FILTER"]
    lot = filters.get("MARKET_LOT_SIZE") or filters["LOT_SIZE"]
    notional = filters.get("MIN_NOTIONAL")
    if not notional:
        raise ValueError("notional filter missing")
    min_notional = notional.get("notional") or notional.get("minNotional")
    return {
        "tick_size": Decimal(price["tickSize"]),
        "step_size": Decimal(lot["stepSize"]),
        "min_qty": Decimal(lot["minQty"]),
        "min_notional": Decimal(min_notional),
    }


def leveraged_order_plan(side, entry, stop, free_usdt, risk_usdt, leverage, rules):
    """Size a leveraged Futures position by the same fixed-dollar-risk method
    as Spot's protective_order_plan, with an added margin cap: the position
    may never demand more than the allocated free capital, even at the
    requested leverage. Sizing is driven by unit_risk (the real ATR-based
    stop distance), never by leverage -- leverage only changes how much
    margin is tied up to hold that same fixed dollar risk, not the risk
    itself."""
    entry = Decimal(str(entry))
    stop = Decimal(str(stop))
    free_usdt = Decimal(str(free_usdt))
    risk_usdt = Decimal(str(risk_usdt))
    leverage = Decimal(str(leverage))
    if side not in ("long", "short"):
        raise ValueError("side must be 'long' or 'short'")
    if side == "long" and not (entry > stop > 0):
        raise ValueError("invalid long order inputs")
    if side == "short" and not (Decimal("0") < entry < stop):
        raise ValueError("invalid short order inputs")
    if not (free_usdt > 0 and risk_usdt > 0 and leverage > 0):
        raise ValueError("invalid live order inputs")
    unit_risk = abs(entry - stop)
    margin_cap_qty = (free_usdt * Decimal("0.9") * leverage) / entry
    raw_qty = min(risk_usdt / unit_risk, margin_cap_qty)
    qty = _down(raw_qty, rules["step_size"])
    # Round the stop further from entry by less than one tick, matching
    # protective_order_plan's bias for the long stop (_down, since a long
    # stop sits below entry) mirrored for a short stop (_up, since it sits
    # above entry) -- never rounds toward a tighter, accidentally-worse stop.
    stop_price = _down(stop, rules["tick_size"]) if side == "long" else _up(stop, rules["tick_size"])
    if qty < rules["min_qty"] or qty * entry < rules["min_notional"]:
        raise ValueError("order is below Binance minimums")
    planned_loss = qty * abs(entry - stop_price)
    if planned_loss > risk_usdt * Decimal("1.05"):
        raise ValueError("rounded plan exceeds risk budget")
    margin_usdt = (qty * entry) / leverage
    if margin_usdt > free_usdt:
        raise ValueError("plan exceeds available margin")
    return {
        "quantity": format(qty, "f"),
        "stop_price": format(stop_price, "f"),
        "planned_loss_usdt": format(planned_loss, "f"),
        "margin_usdt": format(margin_usdt, "f"),
        "leverage": int(leverage),
        "side": side,
    }


def liquidation_is_safe(side, stop_price, liquidation_price, buffer_fraction=0.2):
    """True only if the protective stop would unwind the position well
    before Binance's real liquidation engine could -- checked against the
    ACTUAL liquidation price Binance reports after the position opens
    (never estimated locally; Binance's own maintenance-margin tiers are
    the only trustworthy source). A liquidation_price of 0 (no position /
    not marginable) is never considered safe."""
    stop_price = Decimal(str(stop_price))
    liquidation_price = Decimal(str(liquidation_price))
    buffer = Decimal(str(buffer_fraction))
    if liquidation_price <= 0:
        return False
    if side == "long":
        return stop_price > liquidation_price * (Decimal("1") + buffer)
    return stop_price < liquidation_price * (Decimal("1") - buffer)


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
