"""Fail-closed pilot limits shared by the future live execution worker."""
from decimal import Decimal
from datetime import datetime, timezone


def trade_risk_usdt(config, equity, breaks_up=None):
    """Risk per trade in USDT. risk_per_trade_fraction (23 Sep 2026, user's
    decision: 0.75%) makes it a share of this system's CURRENT equity, so
    it grows with profit and shrinks after losses; without it the fixed
    risk_per_trade_usdt applies (was 5 USDT on a ~37 USDT 4H slice, ~13.5%
    per trade -- far past what a 30R+ losing stretch can survive)."""
    fraction = config.get("risk_per_trade_fraction")
    if fraction and equity and Decimal(str(equity)) > 0:
        return Decimal(str(equity)) * Decimal(str(fraction)) * signal_risk_multiplier(config, breaks_up)
    return Decimal(str(config["risk_per_trade_usdt"]))


def signal_risk_multiplier(config, breaks_up):
    """quality_risk_multipliers [breakout, momentum] (25 Sep 2026, user's
    decision): a long that actually broke a Donchian high (breaks_up >= 1)
    risks more than a momentum-only entry. Over 3 years the breakout entries
    earned about twice as much per trade; with 1.5 / 0.5 and the 30/70
    capital split the out-of-sample last year went from +122% to +203%
    (drawdown 29% -> 32%). An unknown signal type (no breaks_up) keeps the
    plain size rather than guessing."""
    multipliers = config.get("quality_risk_multipliers")
    if not multipliers or breaks_up is None:
        return Decimal("1")
    breakout, momentum = (Decimal(str(x)) for x in multipliers)
    return breakout if int(breaks_up) >= 1 else momentum


def max_trade_risk_usdt(config, equity):
    """Hard ceiling a trade may be rounded UP to when its 0.75% size falls
    under Binance's minimum order (see live_execution.leveraged_order_plan).
    None -- no rounding up, the trade is skipped -- when not configured."""
    fraction = config.get("max_risk_per_trade_fraction")
    if fraction and equity and Decimal(str(equity)) > 0:
        return Decimal(str(equity)) * Decimal(str(fraction))
    return None


def loss_limits(config, equity, baseline=None):
    """(daily, pilot) loss limits in USDT. The *_fraction forms scale with
    the system: daily as a share of current equity, pilot drawdown as a
    share of the baseline it is measured from -- the pilot status's
    deposit-based one when given, else pilot_capital_usdt."""
    daily = Decimal(str(config.get("daily_loss_limit_usdt", 0)))
    pilot = Decimal(str(config.get("pilot_loss_limit_usdt", 0)))
    if config.get("daily_loss_limit_fraction") and equity:
        daily = Decimal(str(equity)) * Decimal(str(config["daily_loss_limit_fraction"]))
    if config.get("pilot_loss_limit_fraction"):
        base = Decimal(str(baseline)) if baseline else Decimal(str(config["pilot_capital_usdt"]))
        pilot = base * Decimal(str(config["pilot_loss_limit_fraction"]))
    return daily, pilot


def may_open(config, open_positions, buys_today, realized_loss_today, pilot_drawdown,
            symbol=None, held_symbols=(), equity=None, baseline=None):
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
    daily_limit, pilot_limit = loss_limits(config, equity, baseline)
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
