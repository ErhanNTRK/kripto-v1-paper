import math


def validate_config(c):
    if any(isinstance(v, (int, float)) and not math.isfinite(v) for v in c.values()):
        raise ValueError('Non-finite configuration value')
    if not 0 < c['risk_fraction'] <= 0.005:
        raise ValueError('Risk per position must be <= 0.5%')
    if c['min_reward_risk'] < 2:
        raise ValueError('Reward/risk must be >= 2')
    if not 0 <= c['fee'] < 0.02 or not 0 <= c['slippage'] < 0.02:
        raise ValueError('Invalid transaction costs')
    if c['initial_cash'] <= 0 or not 0 < c['daily_loss_fraction'] <= 0.05:
        raise ValueError('Invalid capital / daily loss cap')
    for key in ('max_positions', 'max_consecutive_losses', 'top_n', 'breakout_bars', 'support_bars'):
        if not isinstance(c[key], int) or c[key] < 1:
            raise ValueError('Invalid ' + key)
    for key in ('atr_multiplier', 'trailing_atr', 'volume_multiple'):
        if not math.isfinite(c[key]) or c[key] <= 0:
            raise ValueError('Invalid ' + key)
    if not 0 <= c['rsi_min'] <= c['rsi_max'] <= 100:
        raise ValueError('Invalid RSI bounds')
    return c


def size_position(equity, cash, entry, stop, c):
    if stop <= 0 or entry <= stop:
        return None
    # Include entry + exit commissions and adverse stop fill slippage.
    unit_risk = entry * (1 + c['fee']) - stop * (1 - c['slippage']) * (1 - c['fee'])
    qty = min(equity * c['risk_fraction'] / unit_risk, cash / (entry * (1 + c['fee'])))
    if qty * entry < 10:
        return None
    target = (entry * (1 + c['fee']) + c['min_reward_risk'] * unit_risk) / ((1 - c['slippage']) * (1 - c['fee']))
    return dict(qty=qty, entry=entry, stop=stop, target=target,
                initial_risk=unit_risk * qty, unit_risk=unit_risk, high=entry)
