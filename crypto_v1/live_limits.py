"""Fail-closed pilot limits shared by the future live execution worker."""
from decimal import Decimal
from datetime import datetime, timezone


def trade_risk_usdt(config, equity):
    """Risk per trade in USDT. risk_per_trade_fraction (23 Sep 2026, user's
    decision: 0.75%) makes it a share of this system's CURRENT equity, so
    it grows with profit and shrinks after losses; without it the fixed
    risk_per_trade_usdt applies (was 5 USDT on a ~37 USDT 4H slice, ~13.5%
    per trade -- far past what a 30R+ losing stretch can survive)."""
    fraction = config.get("risk_per_trade_fraction")
    if fraction and equity and Decimal(str(equity)) > 0:
        return Decimal(str(equity)) * Decimal(str(fraction))
    return Decimal(str(config["risk_per_trade_usdt"]))


def loss_limits(config, equity):
    """(daily, pilot) loss limits in USDT. The *_fraction forms scale with
    the system: daily as a share of current equity, pilot drawdown as a
    share of the pilot_capital_usdt baseline it is measured from."""
    daily = Decimal(str(config.get("daily_loss_limit_usdt", 0)))
    pilot = Decimal(str(config.get("pilot_loss_limit_usdt", 0)))
    if config.get("daily_loss_limit_fraction") and equity:
        daily = Decimal(str(equity)) * Decimal(str(config["daily_loss_limit_fraction"]))
    if config.get("pilot_loss_limit_fraction"):
        pilot = Decimal(str(config["pilot_capital_usdt"])) * Decimal(str(config["pilot_loss_limit_fraction"]))
    return daily, pilot


def may_open(config, open_positions, buys_today, realized_loss_today, pilot_drawdown,
            symbol=None, held_symbols=(), equity=None):
    """symbol/held_symbols (default: no check, for backward compatibility
    with any caller that hasn't been updated) guard against opening a
    SECOND position in a symbol we already hold. Needed once entries are
    automatic (18 Sep 2026): a strong, sustained breakout can keep
    satisfying long_entry/short_entry for several consecutive bars, and
    without this a human was the only thing preventing a re-fired
    candidate from doubling up on the same symbol -- open_positions alone
    only caps the TOTAL count, not per-symbol."""
    if not config.get("live_trading_enabled", False):
        return False, "live_disabled"
    if symbol is not None and symbol in held_symbols:
        return False, "already_holding_symbol"
    if open_positions >= config["max_open_positions"]:
        return False, "position_limit"
    if buys_today >= config["max_buys_per_day"]:
        return False, "daily_buy_limit"
    daily_limit, pilot_limit = loss_limits(config, equity)
    if Decimal(str(realized_loss_today)) >= daily_limit:
        return False, "daily_loss_limit"
    if Decimal(str(pilot_drawdown)) >= pilot_limit:
        return False, "pilot_loss_limit"
    return True, "allowed"


def confirmed_signal(command, pending, now_ms, config):
    """Accept AL only for one recent pending signal; never guess a symbol."""
    if command.strip().upper() != config["telegram_buy_command"]:
        return None, "invalid_command"
    if len(pending) != 1:
        return None, "pending_signal_count"
    signal = pending[0]
    age = now_ms - int(signal["created_at"])
    if age < 0 or age > config["signal_confirmation_expiry_minutes"] * 60_000:
        return None, "signal_expired"
    return signal, "confirmed"


def exit_action(reason, config):
    """Apply the configured automatic exit policy without predicting prices."""
    risk_reasons = {"stop_loss", "daily_loss_limit", "pilot_loss_limit", "emergency_risk"}
    profit_reasons = {"take_profit", "trailing_profit", "profit_signal"}
    if reason in risk_reasons:
        if not config.get("automatic_risk_exits", False):
            return "blocked", "automatic_risk_exit_disabled"
        return "sell_now", "risk_exit"
    if reason in profit_reasons:
        if config.get("profit_exit_confirmation_required", False):
            return "notify_and_wait", "profit_confirmation_required"
        return "sell_now", "profit_exit"
    return "blocked", "unknown_exit_reason"


def confirmed_profit_exit(command, pending, now_ms, config):
    if command.strip().upper() != config.get("telegram_sell_command", "SAT"):
        return None, "invalid_command"
    if len(pending) != 1:
        return None, "pending_exit_count"
    exit_signal = pending[0]
    if exit_signal.get("reason") not in {"take_profit", "trailing_profit", "profit_signal"}:
        return None, "not_profit_exit"
    age = now_ms - int(exit_signal["created_at"])
    expiry = config.get("profit_exit_confirmation_expiry_minutes", 10) * 60_000
    if age < 0 or age > expiry:
        return None, "exit_signal_expired"
    return exit_signal, "confirmed"
