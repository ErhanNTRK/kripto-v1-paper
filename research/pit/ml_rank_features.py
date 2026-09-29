import numpy as np
from numpy.lib.stride_tricks import sliding_window_view as win


def arr(rows, k):
    return np.array([r[k] for r in rows], dtype=float)


def rolling(x, n, fn):
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        out[n - 1:] = fn(win(x, n), axis=1)
    return out


def ema(x, n):
    out = np.empty(len(x)); k = 2 / (n + 1); e = x[0]
    for i, v in enumerate(x):
        e = v * k + e * (1 - k); out[i] = e
    return out


def wilder(x, n):
    out = np.full(len(x), np.nan)
    if len(x) < n: return out
    r = np.nanmean(x[:n]); out[n - 1] = r
    for i in range(n, len(x)):
        r = (r * (n - 1) + x[i]) / n; out[i] = r
    return out


def features(rows):
    o, h, l, c, v = (arr(rows, k) for k in "ohlcv")
    n = len(c)
    prev = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(abs(h - prev), abs(l - prev)))
    atr = wilder(tr, 14)
    f = {}
    lc = np.log(c)
    for k in (1, 3, 6, 12, 42, 84, 180):
        f[f"r{k}"] = np.concatenate([np.full(k, np.nan), lc[k:] - lc[:-k]])
    f["atr_pct"] = atr / c
    f["vol42"] = rolling(np.concatenate([[0], np.diff(lc)]), 42, np.std)
    for k in (42, 180):
        hi, lo = rolling(h, k, np.max), rolling(l, k, np.min)
        f[f"range{k}"] = (hi - lo) / c
        f[f"dist_hi{k}"] = c / hi - 1
        f[f"dist_lo{k}"] = c / lo - 1
    f["compress"] = f["range42"] / f["range180"]
    # "base": share of the last 180 closes within +-20% of their median, measured
    # on the window that ENDED 42 bars ago (was it a flat base before the recent move?)
    med = rolling(c, 180, np.median)
    base = np.full(n, np.nan)
    if n >= 180:
        w = win(c, 180)
        base[179:] = (np.abs(w / med[179:, None] - 1) <= 0.2).mean(axis=1)
    f["base_before"] = np.concatenate([np.full(42, np.nan), base[:-42]])
    f["base_now"] = base
    va20, va180 = rolling(v, 20, np.mean), rolling(v, 180, np.mean)
    f["vol_ratio"] = v / va20
    f["vol_trend"] = va20 / va180
    green = np.zeros(n)
    for i in range(1, n):
        green[i] = green[i - 1] + 1 if c[i] > o[i] else 0
    f["green_run"] = green
    d = np.diff(c, prepend=c[0]); up, dn = wilder(np.maximum(d, 0), 14), wilder(np.maximum(-d, 0), 14)
    f["rsi"] = 100 - 100 / (1 + up / np.where(dn == 0, np.nan, dn))
    for k in (20, 50, 200):
        f[f"ema{k}"] = c / ema(c, k) - 1
    mid = lambda k: (rolling(h, k, np.max) + rolling(l, k, np.min)) / 2
    tenkan, kijun, span_b = mid(9), mid(26), mid(52)
    span_a = (tenkan + kijun) / 2
    cloud_a = np.concatenate([np.full(26, np.nan), span_a[:-26]]); cloud_b = np.concatenate([np.full(26, np.nan), span_b[:-26]])
    f["ichi_cloud"] = (c - np.fmax(cloud_a, cloud_b)) / atr
    f["ichi_tk"] = (tenkan - kijun) / atr
    f["ichi_ahead"] = (span_a - span_b) / atr
    f["ichi_chikou"] = np.concatenate([np.full(26, np.nan), c[26:] / c[:-26] - 1])
    rng = np.where(h - l == 0, np.nan, h - l)
    f["upper_wick"] = (h - np.maximum(o, c)) / rng
    f["body"] = (c - o) / rng
    for k in (20, 40):
        hi_prev = np.concatenate([[np.nan], rolling(h, k, np.max)[:-1]])
        f[f"break{k}"] = (c > hi_prev).astype(float)
    return f, dict(o=o, h=h, l=l, c=c, atr=atr)


