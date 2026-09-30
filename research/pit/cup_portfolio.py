"""Portfolio test of the cup rim breakout (30 Sep 2026) as its own pot: 170 USDT, daily bars from the point-in-time
4H data, both eras, 12-month windows starting every month + full path, costs 0.07%/side.
Entry: next open after the first daily close above the cup rim (see cup_breakout.py), BTC above its 200-day and
50-day averages. Size: 1.5% risk of the pot against a 2-ATR stop, at most 10 positions, total notional <= 3x
the pot. Exit: stop, else the close 20 days after entry. Compared with the control (any 120-day closing-high
breakout, same rules) and cup+handle."""
import os, json, statistics as S
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
PIT = Path(os.environ["PIT_DIR"]); TAG = "2021-23" if "pit2020" in str(PIT) else "2023-26"
DAY, COST = 86_400_000, 0.0007
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
btc_ok = {r["t"]: i >= 200 and r["c"] > sum(bc[i - 199:i + 1]) / 200 and r["c"] > sum(bc[i - 49:i + 1]) / 50
          for i, r in enumerate(btc)}
BARS, EV = {}, {"cup": defaultdict(list), "handle": defaultdict(list), "control": defaultdict(list)}
for s, rows4 in raw.items():
    rows = to_daily(rows4)
    if len(rows) < 200:
        continue
    BARS[s] = {r["t"]: r for r in rows}
    closes = [r["c"] for r in rows]
    for i in range(121, len(rows) - 1):
        d = rows[i]["t"]
        if s not in daily_top.get(d, ()) or not rows[i].get("atr") or not btc_ok.get(d):
            continue
        prior_high = max(closes[i - 120:i])
        if not (closes[i] > prior_high and closes[i - 1] <= prior_high):
            continue
        entry_day, stop = rows[i + 1]["t"], rows[i + 1]["o"] - 2 * rows[i]["atr"]
        EV["control"][entry_day].append((s, stop))
        p = max(range(i - 120, i), key=lambda k: closes[k]); P = closes[p]
        if i - p < 20 or i - p > 180:
            continue
        tq = min(range(p + 1, i), key=lambda k: rows[k]["l"]); T = rows[tq]["l"]
        if not (0.20 <= 1 - T / P <= 0.50 and tq - p >= 7 and i - tq >= 10 and max(closes[p + 1:i]) <= P):
            continue
        EV["cup"][entry_day].append((s, stop))
        w = rows[max(tq, i - 20):i]
        k = max(range(len(w)), key=lambda k: w[k]["h"])
        if w[k]["h"] >= 0.9 * P and 0.05 <= 1 - min(x["l"] for x in w[k:]) / w[k]["h"] <= 0.15:
            EV["handle"][entry_day].append((s, stop))
del raw
days = sorted({t for b in BARS.values() for t in b})


def run(kind, s0=0, s1=10**15, risk=0.015, maxpos=10, lev=3, hold=20):
    cash, pos, curve = 170.0, {}, []
    for d in days:
        eq = cash + sum(p["qty"] * BARS[s].get(d, {"c": p["mark"]})["c"] for s, p in pos.items())
        if s0 <= d < s1:
            for s, stop in EV[kind].get(d, []):
                r = BARS[s].get(d)
                if not r or s in pos or len(pos) >= maxpos:
                    continue
                entry = r["o"] * (1 + COST)
                if entry <= stop:
                    continue
                qty = eq * risk / (entry - stop)
                notional = sum(p["qty"] * p["mark"] for p in pos.values())
                qty = min(qty, max(0.0, (lev * eq - notional)) / entry)
                if qty * entry < 5:
                    continue
                cash -= qty * entry
                pos[s] = dict(qty=qty, stop=stop, until=d + hold * DAY, mark=r["c"])
        for s, p in list(pos.items()):
            r = BARS[s].get(d)
            if not r:
                continue
            if r["l"] <= p["stop"]:
                cash += p["qty"] * min(r["o"], p["stop"]) * (1 - COST); del pos[s]; continue
            p["mark"] = r["c"]
            if d >= p["until"]:
                cash += p["qty"] * r["c"] * (1 - COST); del pos[s]
        curve.append((d, cash + sum(p["qty"] * p["mark"] for p in pos.values())))
    return curve


ts = lambda y, m: int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)
starts = ([ts(2021 + (m - 1) // 12, (m - 1) % 12 + 1) for m in range(1, 22)] if TAG == "2021-23"
          else [ts(2023 + (m - 1) // 12, (m - 1) % 12 + 1) for m in range(9, 33)])
def dd(c):
    pk, m = c[0][1], 0.0
    for _, e in c:
        pk = max(pk, e); m = max(m, 1 - e / pk)
    return m
print(f"=== {TAG} | olay sayisi: " + ", ".join(f"{k} {sum(len(v) for v in EV[k].values())}" for k in EV))
for kind in ("cup", "handle", "control"):
    c = run(kind)
    rets = []
    for s0 in starts:
        w = [x for x in run(kind, s0, s0 + 365 * DAY) if s0 <= x[0] <= s0 + 365 * DAY]
        rets.append(w[-1][1] / 170 - 1 if w else 0)
    sr = sorted(rets)
    name = {"cup": "CANAK kenar", "handle": "CANAK + kulp", "control": "kontrol (her 120g zirve)"}[kind]
    print(f"  {name:26} tum donem {c[-1][1]/170:5.2f}x dusus %{100*dd(c):3.0f} | 100$ 12 ay: tipik {100*(1+S.median(rets)):5.0f}$ "
          f"en kotu {100*(1+sr[0]):5.0f}$ en iyi {100*(1+sr[-1]):5.0f}$ zararli %{100*sum(x<0 for x in rets)/len(rets):3.0f}", flush=True)
print("BITTI")
