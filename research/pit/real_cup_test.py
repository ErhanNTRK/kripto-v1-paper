"""A REAL cup-with-handle vs a plain breakout with the same exits (30 Sep 2026, after ASTERUSDT: the old short-cup
detector bought a 2-day V drop and a flat week). Definition from the web research (O'Neil, Bulkowski, TradingView
detectors), closed bars only, pivots confirmed K bars late:
  1 left rim L: pivot high (K each side), every high between the rims under max(L, R) * 1.001
  2 prior uptrend: L >= UP x the lowest low of the MAXLEN bars before L
  3 cup length L..R within [MINLEN, MAXLEN] bars
  4 depth D = (L - bottom) / L within [DMIN, DMAX] and 3-12 ATR
  5 rims: |R - L| / L <= 3% and L >= 0.99 R
  6 roundness: y = a t^2 + b t + c on the closes L..R (t in [-1, 1]): a > 0, R^2 >= 0.65, vertex in |t| <= 0.4,
    fitted minimum within 0.25 D of the bottom
  7 no V: each side >= 30% of the length, no single close-to-close drop > 35% of D, >= max(5, 20%) closes in
    the bottom quarter, >= 3 lows within 0.15 D of the bottom
  8 handle R..breakout: 5 bars .. 25% of the cup, low above the cup's midpoint, pullback <= 40% of (R - bottom)
    and <= 12%, handle closes not rising
  10 breakout: close > handle high + 0.1 ATR (first close above), close <= handle high x 1.03
  11 BTC above its EMA50 on the same timeframe
  12 exits: stop = handle low - 0.5 ATR (skip if > 8% away); half at + 0.5 cup height, then stop to break-even;
     the rest at + the full cup height; time stop 2 x handle bars if +1R not reached, 60 bars at most
Baseline: the first close above the highest high of the last 60 bars, stop = lowest low of the last 10 bars -
0.5 ATR, "cup height" = the 60-bar range, the same exits.
Volume is not in the point-in-time data (conditions 9, 10b skipped).
Portfolio per 100 USD: 1.5% risk per trade, at most 6 open, 3x notional cap, entries only on that day's top-50."""
import json, os, statistics as S, sys, time
from bisect import bisect_left
from pathlib import Path
from datetime import datetime, timezone
PIT = Path(os.environ["PIT_DIR"]); TAG = "2021-23" if "pit2020" in str(PIT) else "2023-26"
DAY = 86_400_000
COST = 0.0005 + 0.0002
DAILY = {int(k): set(v) - {"BTCUSDT"} for k, v in json.loads((PIT / "daily_top50.json").read_text()).items()}
EVER = set().union(*DAILY.values()); START = min(DAILY); END = max(DAILY) + DAY
K = 5
from collections import Counter
FUNNEL = Counter()
PARAMS = {"4h": dict(MINLEN=30, MAXLEN=150, DMIN=0.12, DMAX=0.35, UP=1.25, R2=0.65, RIM=0.03),
          "2h": dict(MINLEN=36, MAXLEN=180, DMIN=0.08, DMAX=0.30, UP=1.20, R2=0.65, RIM=0.03)}


def cut(rows):
    keep = rows[:1]
    for a, b in zip(rows, rows[1:]):
        if a["c"] > 0 and (b["o"] / a["c"] > 3 or b["o"] / a["c"] < 1 / 3 or b["t"] - a["t"] > 3 * DAY):
            break
        keep.append(b)
    return keep


def atr_series(rows, n=14):
    out, a = [], None
    for j, r in enumerate(rows):
        tr = r["h"] - r["l"] if j == 0 else max(r["h"] - r["l"], abs(r["h"] - rows[j - 1]["c"]), abs(r["l"] - rows[j - 1]["c"]))
        a = tr if a is None else a + (tr - a) / n
        out.append(a)
    return out


def quad_fit(ys):
    n = len(ys)
    ts = [-1 + 2 * j / (n - 1) for j in range(n)]
    s = [sum(t ** k for t in ts) for k in range(5)]
    r = [sum(y * t ** k for y, t in zip(ys, ts)) for k in range(3)]
    m = [[s[4], s[3], s[2], r[2]], [s[3], s[2], s[1], r[1]], [s[2], s[1], s[0], r[0]]]
    for c in range(3):
        p = max(range(c, 3), key=lambda x: abs(m[x][c])); m[c], m[p] = m[p], m[c]
        if abs(m[c][c]) < 1e-18:
            return None
        for x in range(3):
            if x != c:
                f = m[x][c] / m[c][c]; m[x] = [u - f * v for u, v in zip(m[x], m[c])]
    a, b, c = (m[k][3] / m[k][k] for k in range(3))
    mean = sum(ys) / n
    ss_tot = sum((y - mean) ** 2 for y in ys) or 1e-18
    ss_res = sum((y - (a * t * t + b * t + c)) ** 2 for y, t in zip(ys, ts))
    return a, b, c, 1 - ss_res / ss_tot


def find_cups(rows, atr, P, btc_ok):
    """(bar index of the breakout, stop, height) for every cup-with-handle breakout."""
    H = [r["h"] for r in rows]; Lo = [r["l"] for r in rows]; C = [r["c"] for r in rows]
    pivots = [j for j in range(K, len(rows) - K) if H[j] == max(H[j - K:j + K + 1])]
    out, last_r = [], -1
    for i in range(1, len(rows)):
        if not btc_ok(rows[i]["t"]):
            continue
        # right rim: the latest confirmed pivot with a handle of 5+ bars before this bar
        pi = bisect_left(pivots, i - 4) - 1
        while pi >= 0 and pivots[pi] + K > i:
            pi -= 1
        if pi < 0:
            continue
        r = pivots[pi]; h = i - r - 1
        if h < 5 or r == last_r:
            continue
        hh = max(H[r + 1:i]); hl = min(Lo[r + 1:i])
        level = hh + 0.1 * atr[i]
        if not (C[i] > level and C[i - 1] <= level and C[i] <= hh * 1.03):
            continue
        for pj in range(pi - 1, -1, -1):
            l = pivots[pj]; n = r - l
            if n < P["MINLEN"]:
                continue
            if n > P["MAXLEN"]:
                break
            L, R = H[l], H[r]
            FUNNEL["1 aday"] += 1
            if abs(R - L) / L > P["RIM"] or L < (1 - P["RIM"] / 3) * R or max(H[l + 1:r]) > max(L, R) * 1.001:
                continue
            FUNNEL["2 rimler"] += 1
            if h > 0.25 * n:
                continue
            pre = Lo[max(0, l - P["MAXLEN"]):l]
            if not pre or L < P["UP"] * min(pre):
                continue
            FUNNEL["3 onceki yukselis"] += 1
            b = min(range(l + 1, r), key=lambda j: Lo[j]); B = Lo[b]
            D = L - B
            if not (P["DMIN"] <= D / L <= P["DMAX"] and 3 * atr[l] <= D <= 12 * atr[l]):
                continue
            FUNNEL["4 derinlik"] += 1
            if (b - l) < 0.3 * n or (r - b) < 0.3 * n:
                continue
            FUNNEL["5 dengeli"] += 1
            closes = C[l:r + 1]
            if max(closes[j - 1] - closes[j] for j in range(1, len(closes))) > 0.35 * D:
                continue
            if sum(c <= B + 0.25 * D for c in closes) < max(5, 0.2 * n):
                continue
            if sum(x <= B + 0.15 * D for x in Lo[l:r + 1]) < 3:
                continue
            FUNNEL["6 V degil"] += 1
            fit = quad_fit(closes)
            if not fit:
                continue
            a, bb, cc, r2 = fit
            if a <= 0 or r2 < P["R2"]:
                continue
            tv = -bb / (2 * a)
            if abs(tv) > 0.4 or abs((cc - bb * bb / (4 * a)) - B) > 0.25 * D:
                continue
            FUNNEL["7 yuvarlak"] += 1
            if hl <= L - D / 2 or R - hl > 0.4 * (R - B) or (R - hl) / R > 0.12:
                continue
            hc = C[r + 1:i]
            if len(hc) >= 2:
                m = (len(hc) - 1) / 2; mc = sum(hc) / len(hc)
                if sum((j - m) * (y - mc) for j, y in enumerate(hc)) > 0:
                    continue
            FUNNEL["8 kulp"] += 1
            stop = hl - 0.5 * atr[i]
            out.append((i, stop, R - B, h)); last_r = r
            break
    return out


def find_breakouts(rows, atr, btc_ok, n=60):
    H = [r["h"] for r in rows]; Lo = [r["l"] for r in rows]; C = [r["c"] for r in rows]
    out = []
    for i in range(n + 1, len(rows)):
        if not btc_ok(rows[i]["t"]):
            continue
        top = max(H[i - n:i]); prev_top = max(H[i - n - 1:i - 1])
        if C[i] > top and C[i - 1] <= prev_top:
            out.append((i, min(Lo[i - 10:i]) - 0.5 * atr[i], top - min(Lo[i - n:i]), 10))
    return out


def trade(rows, i, stop, height, h):
    """Enter at the next bar's open. Returns (entry time, exit time, return on price, R multiple) or None."""
    if i + 1 >= len(rows):
        return None
    e = rows[i + 1]["o"]
    if stop <= 0 or e <= stop or (e - stop) / e > 0.08:
        return None
    risk = e - stop; tp1, tp2 = e + 0.5 * height, e + height
    half_done, got = False, 0.0
    t_limit = i + 1 + min(60, max(2 * h, 10))
    for j in range(i + 1, min(len(rows), i + 1 + 60)):
        r = rows[j]
        if r["l"] <= stop:
            px = min(r["o"], stop) if j > i + 1 else stop
            got += (0.5 if half_done else 1.0) * (px / e - 1)
            return rows[i + 1]["t"], r["t"], got - COST * 2, (got - COST * 2) * e / risk
        if not half_done and r["h"] >= tp1:
            got += 0.5 * (max(r["o"], tp1) / e - 1); half_done = True; stop = max(stop, e * (1 + COST * 2))
        if half_done and r["h"] >= tp2:
            got += 0.5 * (max(r["o"], tp2) / e - 1)
            return rows[i + 1]["t"], r["t"], got - COST * 2, (got - COST * 2) * e / risk
        if not half_done and j >= t_limit and r["c"] < e + risk:
            got += r["c"] / e - 1
            return rows[i + 1]["t"], r["t"], got - COST * 2, (got - COST * 2) * e / risk
    j = min(len(rows) - 1, i + 60); r = rows[j]
    got += (0.5 if half_done else 1.0) * (r["c"] / e - 1)
    return rows[i + 1]["t"], r["t"], got - COST * 2, (got - COST * 2) * e / risk


def portfolio(trades, s0, s1, risk=0.015, maxpos=6):
    eq, open_ = 100.0, []
    for t_in, t_out, ret, rr, stop_frac in sorted(x for x in trades if s0 <= x[0] < s1):
        still = []
        for o in open_:
            if o[0] <= t_in:
                eq += o[1]
            else:
                still.append(o)
        open_ = still
        if len(open_) >= maxpos:
            continue
        notional = min(eq * risk / stop_frac, eq * 3 - sum(o[2] for o in open_))
        if notional <= 5:
            continue
        open_.append((t_out, notional * ret, notional))
    return eq + sum(o[1] for o in open_)


ts = lambda y, m: int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)
STARTS = ([ts(2021 + (m - 1) // 12, (m - 1) % 12 + 1) for m in range(1, 22)] if TAG == "2021-23"
          else [ts(2023 + (m - 1) // 12, (m - 1) % 12 + 1) for m in range(9, 33)])
YEAR = 365 * DAY

if __name__ == "__main__":
    t0 = time.time()
    for fl in ("4h", "2h"):
        raw = json.loads((PIT / f"data_{fl}.json").read_text(encoding="utf-8"))
        btc = raw.pop("BTCUSDT"); ema, e = {}, None
        for r in btc:
            e = r["c"] if e is None else e + (r["c"] - e) * 2 / 51; ema[r["t"]] = r["c"] > e
        btc_ok = lambda t: ema.get(t, False)
        found = {"GERCEK CANAK": [], "GEVSEK CANAK": [], "DUZ KIRILIM (60 mum)": []}; FUNNEL.clear()
        for s in list(raw):
            rows = cut(raw.pop(s))
            if s not in EVER or len(rows) < 250:
                continue
            atr = atr_series(rows)
            loose = dict(PARAMS[fl], R2=0.45, RIM=0.06, UP=1.10, DMIN=PARAMS[fl]["DMIN"] * 0.7)
            FUNNEL["_tanim"] += 0
            strict = find_cups(rows, atr, PARAMS[fl], btc_ok)
            for name, sigs in (("GERCEK CANAK", strict),
                               ("GEVSEK CANAK", find_cups(rows, atr, loose, btc_ok)),
                               ("DUZ KIRILIM (60 mum)", find_breakouts(rows, atr, btc_ok))):
                for i, stop, height, h in sigs:
                    if rows[i]["t"] < START or s not in DAILY.get(rows[i]["t"] // DAY * DAY, ()):
                        continue
                    tr = trade(rows, i, stop, height, h)
                    if tr:
                        e0 = rows[i + 1]["o"]
                        found[name].append(tr + ((e0 - stop) / e0,))
        print(f"\n=== {TAG} {fl.upper()} ({round(time.time()-t0)} sn) ===", flush=True)
        print("  eleme (siki+gevsek toplam): " + ", ".join(f"{k}: {v}" for k, v in sorted(FUNNEL.items()) if v), flush=True)
        for name, trs in found.items():
            rs = [x[3] for x in trs]
            if not rs:
                print(f"{name:22} islem yok"); continue
            wins = [x for x in rs if x > 0]; loss = -sum(x for x in rs if x <= 0) or 1e-9
            weeks = (END - START) / (7 * DAY)
            pots = [portfolio(trs, s0, s0 + YEAR) for s0 in STARTS]
            line = (f"{name:22} | {len(rs):5d} islem (haftada {len(rs)/weeks:4.1f}) | karli %{100*len(wins)/len(rs):3.0f} | "
                    f"ort {S.mean(rs):+.2f}R | PF {sum(wins)/loss:4.2f} | 100$ 12 ay: tipik {S.median(pots):5.0f}$ "
                    f"en kotu {min(pots):4.0f}$ en iyi {max(pots):5.0f}$")
            if TAG == "2023-26":
                line += f" | SON 12 AY {portfolio(trs, ts(2025, 9), END):4.0f}$ SON 6 AY {portfolio(trs, ts(2026, 3), END):4.0f}$"
            print(line, flush=True)
        del raw
    print("BITTI", flush=True)
