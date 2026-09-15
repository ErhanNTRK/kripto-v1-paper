def btc_ok(btc):
    return bool(btc and btc.get('ema200') and
                btc['c'] > btc['ema20'] > btc['ema50'] > btc['ema200'])


def buy_signal(f, btc, config):
    if not f.get('ema200') or not f.get('atr') or not f.get('volume_avg'):
        return False
    return (btc_ok(btc) and f['c'] > f['ema20'] > f['ema50'] > f['ema200']
            and config['rsi_min'] <= f['rsi'] <= config['rsi_max']
            and f['v'] >= config['volume_multiple'] * f['volume_avg']
            and f['c'] > f['resistance'])


def sell_signal(f, btc, config=None):
    ema_key = 'ema' + str((config or {}).get('exit_ema', 20))
    return not btc_ok(btc) or f['c'] < f[ema_key] or f['rsi'] < 45


def initial_stop(f, config):
    return min(f['c'] - config['atr_multiplier'] * f['atr'], f['support'] - 0.1 * f['atr'])
