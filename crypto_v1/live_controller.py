"""Orchestrate one Telegram-approved buy and its exchange-native protection."""
from decimal import Decimal

from .binance_trade import OrderRejected, OrderStateUnknown
from .live_execution import _down, execution_enabled, protective_order_plan
from .live_limits import confirmed_signal, may_open
from .live_signal import pending_candidates


def _known_or_place(executor, symbol, client_id, place):
    try:
        return executor.query(symbol, client_id)
    except OrderRejected as error:
        if error.code != -2013:  # Binance: order does not exist.
            raise
    try:
        return place()
    except OrderStateUnknown:
        return executor.query(symbol, client_id)


def _emergency_sell(executor, symbol, quantity, update_id):
    exit_id = f"kv1e{int(update_id)}"
    return _known_or_place(executor, symbol, exit_id,
                           lambda: executor.market_sell(symbol, format(quantity, "f"), exit_id))


def approve_buy(update_id, command, now_ms, saved, config, environment, market, executor):
    candidates = pending_candidates(saved, now_ms, config)
    signal, reason = confirmed_signal(command, candidates, now_ms, config)
    if not signal:
        return {"status": "rejected", "reason": reason}
    status = market.pilot_status()
    allowed, reason = may_open(dict(config, live_trading_enabled=True),
                               status["open_positions"], status["buys_today"],
                               status["realized_loss_today"], status["pilot_drawdown"],
                               symbol=signal["symbol"], held_symbols=status.get("held_symbols", ()))
    if not allowed:
        return {"status": "rejected", "reason": reason}
    price = Decimal(str(market.price(signal["symbol"])))
    close = Decimal(str(signal["close"]))
    stop = Decimal(str(signal["stop"]))
    drift = Decimal(str(config.get("max_entry_drift_fraction", 0.005)))
    if price <= stop or price > close * (Decimal("1") + drift):
        return {"status": "rejected", "reason": "entry_price_moved"}
    rules = market.rules(signal["symbol"])
    plan = protective_order_plan(price, stop, status["free_usdt"],
                                 config["risk_per_trade_usdt"], rules)
    plan.update(symbol=signal["symbol"], entry_price=format(price, "f"))
    if not execution_enabled(config, environment):
        return {"status": "preview", "reason": "real_orders_disabled", "plan": plan}
    buy_id = f"kv1b{int(update_id)}"
    quote = format(Decimal(plan["quantity"]) * price, "f")
    buy = _known_or_place(executor, signal["symbol"], buy_id,
                          lambda: executor.market_buy(signal["symbol"], quote, buy_id))
    if buy.get("status") != "FILLED" or Decimal(str(buy.get("executedQty", "0"))) <= 0:
        raise RuntimeError("buy was not fully filled; operator review required")
    # Reserve 0.2% for possible base-asset commission; any remainder is harmless dust.
    stop_qty = _down(Decimal(str(buy["executedQty"])) * Decimal("0.998"), rules["step_size"])
    if stop_qty < rules["min_qty"]:
        raise RuntimeError("filled quantity cannot support a protective order")
    stop_id = f"kv1s{int(update_id)}"
    try:
        stop_order = _known_or_place(
            executor, signal["symbol"], stop_id,
            lambda: executor.protective_stop(signal["symbol"], format(stop_qty, "f"),
                                             plan["stop_price"], plan["stop_limit_price"], stop_id))
    except OrderRejected:
        sold = _emergency_sell(executor, signal["symbol"], stop_qty, update_id)
        return {"status": "bought_then_emergency_sold", "buy_order": buy,
                "emergency_sell": sold, "reason": "protective_stop_rejected", "plan": plan}
    return {"status": "bought_and_protected", "buy_order": buy,
            "stop_order": stop_order, "plan": plan}
