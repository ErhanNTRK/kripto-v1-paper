def ema(values, period):
    result = [None] * len(values)
    if len(values) < period:
        return result
    current = sum(values[:period]) / period
    result[period - 1] = current
    alpha = 2 / (period + 1)
    for i in range(period, len(values)):
        current += alpha * (values[i] - current)
        result[i] = current
    return result


def wilder(values, period):
    result = [None] * len(values)
    if len(values) < period:
        return result
    current = sum(values[:period]) / period
    result[period - 1] = current
    for i in range(period, len(values)):
        current = (current * (period - 1) + values[i]) / period
        result[i] = current
    return result


def features(rows, config):
    closes = [r['c'] for r in rows]
    averages = {p: ema(closes, p) for p in (20, 50, 200)}
    changes = [closes[i] - closes[i - 1] for i in range(1, len(rows))]
    gains = wilder([max(d, 0) for d in changes], 14)
    losses = wilder([max(-d, 0) for d in changes], 14)
    tr = [max(r['h'] - r['l'], abs(r['h'] - rows[i-1]['c']),
              abs(r['l'] - rows[i-1]['c'])) if i else r['h']-r['l']
          for i, r in enumerate(rows)]
    atr = wilder(tr, 14)
    output = []
    for i, row in enumerate(rows):
        f = dict(row, atr=atr[i])
        f.update({f'ema{p}': averages[p][i] for p in averages})
        f['rsi'] = None
        if i >= 14:
            g, loss = gains[i-1], losses[i-1]
            f['rsi'] = 50 if g == loss == 0 else (100 if loss == 0 else 100 - 100 / (1 + g / loss))
        f['volume_avg'] = sum(r['v'] for r in rows[i-20:i]) / 20 if i >= 20 else None
        b, s = config['breakout_bars'], config['support_bars']
        f['resistance'] = max(r['h'] for r in rows[i-b:i]) if i >= b else None
        f['support'] = min(r['l'] for r in rows[i-s:i]) if i >= s else None
        output.append(f)
    return output
