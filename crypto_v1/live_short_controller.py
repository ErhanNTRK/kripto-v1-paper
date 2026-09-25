"""Orchestrate one Telegram-approved short and its exchange-native
protection on Binance Futures. Mirrors live_controller.approve_buy, with
two leverage-specific additions: leverage/isolated-margin are set before
opening, and the REAL liquidation price Binance reports after opening is
checked against the protective stop before that stop is placed -- an
unsafe combination (stop too close to liquidation, e.g. from unexpected
slippage or fee eating into the margin buffer) triggers an immediate
market close instead of ever resting a stop that liquidation could beat."""
import time
from decimal import Decimal

from .binance_trade import OrderRejected, OrderStateUnknown
from .live_controller import _known_or_place
from .live_execution import execution_enabled, leveraged_order_plan, liquidation_is_safe, safe_leverage
from .live_limits import confirmed_signal, may_open, max_trade_risk_usdt, trade_risk_usdt
from .live_signal import pending_candidates, pending_short_candidates
from .short_signal import leverage_for_signal

LIQUIDATION_SAFETY_BUFFER = 0.2  # stop must sit >=20% of the liquidation distance away


def _settle_fill(executor, symbol, client_id, order, attempts=5, sleep=time.sleep):
    """A Futures MARKET order's own response can say NEW with executedQty 0
    even though it fills a moment later. Live 23 Sep 2026: PROVEUSDT's buy
    FILLED on Binance at 13:01:31 while the response read unfilled, the
    controller raised "not fully filled", and the position sat open with no
    protective stop. Ask Binance for the order's real state before giving
    up on it."""
    for _ in range(attempts):
        if order.get("status") == "FILLED" and Decimal(str(order.get("executedQty", "0"))) > 0:
            return order
        sleep(0.5)
        order = executor.query(symbol, client_id)
    return order


def _already_open(executor, market, symbol):
    """Fresh, per-symbol check straight before any order. held_symbols comes
    from pilot data cached up to 90s and shared by the 4H and 2H systems;
    live 23 Sep 2026 23:02 the 4H side bought NILUSDT and the 2H side,
    still reading the pre-buy cache, went for the same symbol seconds later
    -- only a leverage change Binance happened to refuse (-4067) stopped a
    second buy that would have merged into the first position (one-way
    mode) and tangled both systems' stops."""
    for entry in executor.position_risk(symbol):
        if entry.get("symbol") == symbol and abs(float(entry.get("positionAmt", "0"))) > 0:
            return True
    return False


def _already_used(executor, symbol, client_id):
    """This signal's opening order already filled once. Called only after
    _already_open found nothing open, so that position has since been
    closed -- by the emergency close right after it, typically. Without
    this check (audit A8) the next tick's _known_or_place found the old
    filled order, took it for a fresh open, and "emergency-closed" it again
    on every tick for the signal's whole 30-minute window: a Telegram alert
    each time, and a slot of the day's entries used up."""
    try:
        prior = executor.query(symbol, client_id)
    except OrderRejected as error:
        if error.code != -2013:  # Binance: order does not exist.
            raise
        return False
    return (prior or {}).get("status") == "FILLED"


def _opened(market):
    # A fill changes positions, margin and held_symbols for BOTH systems:
    # drop the shared pilot cache so the next decision sees it.
    invalidate = getattr(market, "invalidate_pilot_cache", None)
    if invalidate:
        invalidate()


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
                               status["realized_loss_today"], status["pilot_drawdown"],
                               symbol=signal["symbol"], held_symbols=status.get("held_symbols", ()),
                               equity=status.get("equity"), baseline=status.get("pilot_baseline"))
    if not allowed:
        return {"status": "rejected", "reason": reason}
    price = Decimal(str(market.price(signal["symbol"])))
    close = Decimal(str(signal["close"]))
    stop = Decimal(str(signal["stop"]))
    drift = Decimal(str(config.get("max_entry_drift_fraction", 0.005)))
    if price >= stop or price < close * (Decimal("1") - drift):
        return {"status": "rejected", "reason": "entry_price_moved"}
    # The signal's 3x/5x tier is a ceiling; the stop distance decides (see
    # live_execution.safe_leverage). Under the price-based 20% buffer the
    # short side is the tighter one: a typical 5-9% stop lands on 2x.
    leverage = safe_leverage("short", price, stop, signal["leverage"], LIQUIDATION_SAFETY_BUFFER)
    if leverage is None:
        return {"status": "rejected", "reason": "stop_too_wide_for_safe_leverage"}
    rules = market.rules(signal["symbol"])
    plan = leveraged_order_plan("short", price, stop, status["free_usdt"],
                                trade_risk_usdt(config, status.get("equity")), leverage, rules,
                                max_risk_usdt=max_trade_risk_usdt(config, status.get("equity")))
    plan.update(symbol=signal["symbol"], entry_price=format(price, "f"))
    if not execution_enabled(config, environment):
        return {"status": "preview", "reason": "real_orders_disabled", "plan": plan}
    symbol = signal["symbol"]
    if _already_open(executor, market, symbol):
        return {"status": "rejected", "reason": "already_holding_symbol"}
    open_id = f"kv1fs{int(update_id)}"
    if _already_used(executor, symbol, open_id):
        return {"status": "rejected", "reason": "already_attempted"}
    executor.set_isolated_margin(symbol)
    executor.set_leverage(symbol, plan["leverage"])
    opened = _known_or_place(executor, symbol, open_id,
                             lambda: executor.market_open_short(symbol, plan["quantity"], open_id))
    opened = _settle_fill(executor, symbol, open_id, opened)
    _opened(market)
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


def approve_long_leveraged(update_id, command, now_ms, saved, config, environment, market, executor):
    """Mirrors approve_short exactly, side-flipped: a leveraged LONG on
    Binance Futures, added 21 Sep 2026 per the user's explicit decision
    (4H long entries are strength-tiered 3x/5x, matching the already-
    walk-forward-validated 5/5 GO signal; 2H long entries are a fixed 2x,
    since the 2H signal is NO_GO overall -- 3/5 windows, not all 5 -- so
    its edge doesn't get the same leverage as the fully-validated 4H one).
    Reads the SAME AL_ADAYI candidate feed live_controller.approve_buy
    (unleveraged Spot) used to read -- this REPLACES that path for new
    entries; existing already-open Spot long positions are unaffected and
    keep exiting through the Spot path until they close naturally.

    leverage: an explicit "leverage" on the candidate (2H's fixed value,
    set in github_worker.write_2h_signal_state) is used as-is; otherwise
    (4H, which only carries "breaks_up" -- see backtest.Engine.step) it's
    computed here via leverage_for_signal, mirroring exactly how the
    short side already sizes leverage by breakout strength."""
    candidates = pending_candidates(saved, now_ms, config)
    signal, reason = confirmed_signal(command, candidates, now_ms, config)
    if not signal:
        return {"status": "rejected", "reason": reason}
    status = market.pilot_status()
    allowed, reason = may_open(dict(config, live_trading_enabled=True),
                               status["open_positions"], status["opens_today"],
                               status["realized_loss_today"], status["pilot_drawdown"],
                               symbol=signal["symbol"], held_symbols=status.get("held_symbols", ()),
                               equity=status.get("equity"), baseline=status.get("pilot_baseline"))
    if not allowed:
        return {"status": "rejected", "reason": reason}
    price = Decimal(str(market.price(signal["symbol"])))
    close = Decimal(str(signal["close"]))
    stop = Decimal(str(signal["stop"]))
    drift = Decimal(str(config.get("max_entry_drift_fraction", 0.005)))
    if price <= stop or price > close * (Decimal("1") + drift):
        return {"status": "rejected", "reason": "entry_price_moved"}
    tier = signal.get("leverage")
    if tier is None:
        tier = leverage_for_signal(signal.get("breaks_up", 0))
    leverage = safe_leverage("long", price, stop, tier, LIQUIDATION_SAFETY_BUFFER)
    if leverage is None:
        return {"status": "rejected", "reason": "stop_too_wide_for_safe_leverage"}
    rules = market.rules(signal["symbol"])
    plan = leveraged_order_plan("long", price, stop, status["free_usdt"],
                                trade_risk_usdt(config, status.get("equity"), signal.get("breaks_up")),
                                leverage, rules,
                                max_risk_usdt=max_trade_risk_usdt(config, status.get("equity")))
    plan.update(symbol=signal["symbol"], entry_price=format(price, "f"))
    if not execution_enabled(config, environment):
        return {"status": "preview", "reason": "real_orders_disabled", "plan": plan}
    symbol = signal["symbol"]
    if _already_open(executor, market, symbol):
        return {"status": "rejected", "reason": "already_holding_symbol"}
    open_id = f"kv1fl{int(update_id)}"
    if _already_used(executor, symbol, open_id):
        return {"status": "rejected", "reason": "already_attempted"}
    executor.set_isolated_margin(symbol)
    executor.set_leverage(symbol, plan["leverage"])
    opened = _known_or_place(executor, symbol, open_id,
                             lambda: executor.market_open_long(symbol, plan["quantity"], open_id))
    opened = _settle_fill(executor, symbol, open_id, opened)
    _opened(market)
    filled_qty = Decimal(str(opened.get("executedQty", "0")))
    if opened.get("status") != "FILLED" or filled_qty <= 0:
        raise RuntimeError("long open was not fully filled; operator review required")
    position = _position_for(symbol, executor.position_risk(symbol))
    liquidation_price = position.get("liquidationPrice") if position else "0"
    if not liquidation_is_safe("long", plan["stop_price"], liquidation_price, LIQUIDATION_SAFETY_BUFFER):
        closed = _known_or_place(executor, symbol, f"kv1fk{int(update_id)}",
                                 lambda: executor.market_close_long(symbol, format(filled_qty, "f"),
                                                                     f"kv1fk{int(update_id)}"))
        return {"status": "opened_then_emergency_closed", "open_order": opened,
                "emergency_close": closed, "reason": "liquidation_too_close",
                "liquidation_price": liquidation_price, "plan": plan}
    stop_id = f"kv1fq{int(update_id)}"
    try:
        stop_order = _known_or_place(
            executor, symbol, stop_id,
            lambda: executor.protective_stop_for_long(symbol, format(filled_qty, "f"),
                                                       plan["stop_price"], stop_id))
    except OrderRejected:
        closed = _known_or_place(executor, symbol, f"kv1fk{int(update_id)}",
                                 lambda: executor.market_close_long(symbol, format(filled_qty, "f"),
                                                                     f"kv1fk{int(update_id)}"))
        return {"status": "opened_then_emergency_closed", "open_order": opened,
                "emergency_close": closed, "reason": "protective_stop_rejected", "plan": plan}
    return {"status": "opened_and_protected", "open_order": opened,
            "stop_order": stop_order, "liquidation_price": liquidation_price, "plan": plan}
