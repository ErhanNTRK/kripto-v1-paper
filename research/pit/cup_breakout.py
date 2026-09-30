"""Cup RIM breakout (user, 30 Sep 2026: the cup expectation is popular among Binance traders and moves price).
The earlier cup study (cup_study.py) bought in the middle of the cup and lost; this buys when the RIM breaks.
Daily bars built from the point-in-time 4H data (a day's close = its last 4H close, known that evening),
both eras (PIT_DIR), top-50 of the breakout day, costs 0.07%/side, data cut at symbol re-use jumps.
  rim P   : a close that was the highest close of the 120 days before it
  cup     : afterwards the low falls 20-50% under P (trough T at least 7 days after P), no close above P meanwhile
  round   : the breakout comes at least 10 days after the trough
  breakout: first daily close above P, 20-180 days after P; buy at the next day's open
  handle  : (variant) within the 20 days before the breakout the price was >= 90% of P and then dipped 5-15%
  control : ANY first close above a 120-day closing high (no cup shape required)
Exits: (A) stop 2 daily ATR, trail 3 ATR after +1R, max 60 days; (M) measured move: target P + (P - T),
stop 2 ATR, max 60 days; (H) hold 20 days, stop 2 ATR. Filters: none / BTC above its 200-day and 50-day averages."""
import os, json, statistics as S
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
PIT = Path(os.environ["PIT_DIR"]); TAG = "2021-23" if "pit2020" in str(PIT) else "2023-26"
DAY = 86_400_000
COST = 0.0007
daily_top = {int(k): set(v) for k, v in json.loads((PIT / "daily_top50.json").read_text()).items()}
raw = json.loads((PIT / "data_4h.json").read_text(encoding="utf-8"))


def to_daily(rows):
    keep = rows[:1]
    for a, b in zip(rows, rows[1:]):
        if a["c"] > 0 and (b["o"] / a["c"] > 3 or b["o"] / a["c"] < 1 / 3 or b["t"] - a["t"] > 3 * DAY):
            break
        keep.append(b)
    g = defaultdict(list)
    for r in keep:
        g[r["t"] // DAY * DAY].append(r)
    out = []
    for d in sorted(g):
        b = g[d]
        if len(b) == 6:
            out.append(dict(t=d, o=b[0]["o"], h=max(x["h"] for x in b), l=min(x["l"] for x in b), c=b[-1]["c"]))
    for i in range(len(out)):
        trs = [max(out[j]["h"] - out[j]["l"], abs(out[j]["h"] - out[j - 1]["c"]), abs(out[j]["l"] - out[j - 1]["c"]))
               for j in range(max(1, i - 13), i + 1)]
        out[i]["atr"] = sum(trs) / len(trs) if trs else None
    return out


btc = to_daily(raw.pop("BTCUSDT"))
bc = [r["c"] for r in btc]
btc_ok = {}
for i, r in enumerate(btc):
    if i >= 200:
        btc_ok[r["t"]] = r["c"] > sum(bc[i - 199:i + 1]) / 200 and r["c"] > sum(bc[i - 49:i + 1]) / 50


def trade(rows, i, stop, target=None, trail=None, max_days=60):
    entry = rows[i]["o"] * (1 + COST)
    risk = entry - stop
    if risk <= 0:
        return None
    high = entry
    for j in range(i, min(len(rows), i + max_days)):
        r = rows[j]
        if r["l"] <= stop:
            return ((min(r["o"], stop) if j > i else stop) * (1 - COST) - entry) / risk
        if target and r["h"] >= target:
            return (max(r["o"], target) * (1 - COST) - entry) / risk
        high = max(high, r["h"])
        if trail and high >= entry + risk and r.get("atr"):
            stop = max(stop, high - trail * r["atr"])
    return (rows[min(len(rows), i + max_days) - 1]["c"] * (1 - COST) - entry) / risk


res = defaultdict(list)
for s, rows4 in raw.items():
    rows = to_daily(rows4)
    if len(rows) < 200:
        continue
    closes = [r["c"] for r in rows]
    for i in range(121, len(rows) - 1):
        d = rows[i]["t"]
        if s not in daily_top.get(d, ()) or not rows[i].get("atr"):
            continue
        prior_high = max(closes[i - 120:i])
        if not (closes[i] > prior_high and closes[i - 1] <= prior_high):
            continue                                 # first close above the 120-day closing high
        # find the rim: the day of that prior high
        p = max(range(i - 120, i), key=lambda k: closes[k])
        P = closes[p]
        seg = rows[p + 1:i]
        cup = handle = False
        T = None
        if seg:
            tq = min(range(p + 1, i), key=lambda k: rows[k]["l"])
            T = rows[tq]["l"]
            depth = 1 - T / P
            cup = (0.20 <= depth <= 0.50 and tq - p >= 7 and i - tq >= 10 and 20 <= i - p <= 180
                   and max(closes[p + 1:i]) <= P)
            if cup:
                w = rows[max(tq, i - 20):i]
                peak_k = max(range(len(w)), key=lambda k: w[k]["h"]) if w else None
                if peak_k is not None and w[peak_k]["h"] >= 0.9 * P:
                    after = w[peak_k:]
                    dip = 1 - min(x["l"] for x in after) / w[peak_k]["h"]
                    handle = 0.05 <= dip <= 0.15
        atr = rows[i]["atr"]
        o = rows[i + 1]["o"]
        exits = {"A trend (2ATR stop, 3ATR iz)": trade(rows, i + 1, o - 2 * atr, trail=3.0),
                 "H 20 gun tut (2ATR stop)": trade(rows, i + 1, o - 2 * atr, max_days=20)}
        if cup:
            exits["M olculu hedef (P+(P-T))"] = trade(rows, i + 1, o - 2 * atr, target=P + (P - T))
        kinds = ["kontrol: her 120g zirve kirilimi"]
        if cup:
            kinds.append("CANAK kenar kirilimi")
        if handle:
            kinds.append("CANAK + KULP kirilimi")
        filt = btc_ok.get(d, False)
        for kind in kinds:
            for ex, r in exits.items():
                if r is None:
                    continue
                res[(kind, ex, "filtresiz")].append(r)
                if filt:
                    res[(kind, ex, "BTC 200g+50g ustu")].append(r)


def stats(rs):
    w = sum(x for x in rs if x > 0); l = -sum(x for x in rs if x <= 0)
    return (f"n {len(rs):4d} | kazanan %{100*sum(x>0 for x in rs)/len(rs):3.0f} | ort {S.mean(rs):+.2f}R | "
            f"PF {w/l if l else 0:4.2f} | 5R+ {sum(x>=5 for x in rs):3d} | toplam {sum(rs):+6.0f}R")


print(f"=== {TAG} (gunluk mumlar, gunluk ilk 50) ===")
for k in sorted(res):
    print(f"  {k[0]:34} {k[1]:30} {k[2]:18} {stats(res[k])}", flush=True)
print("BITTI")
