"""Portfolio test of the dip-catcher (29 Sep): resting limit buys at yesterday's close x (1 - depth) on every coin
of the day's top-50; exit = +TP take-profit (not on the fill bar) or stop, else the close after N days.
Capital: 170 USDT; each fill uses `size` x current equity as notional; at most `maxpos` open; a fill that finds no
free slot is skipped. Positions marked at 4H closes. Both eras; full path + 12-month windows every month."""
import json, statistics as S
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
DAY, H4 = 86_400_000, 4 * 3600 * 1000
COST = 0.0007
ts = lambda y, m: int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)

def load(pdir):
    P = Path(rf"C:\Users\ASUS-PC\kripto\arastirma-verisi\{pdir}")
    daily = {int(k): [s for s in v if s != "BTCUSDT"] for k, v in json.loads((P / "daily_top50.json").read_text()).items()}
    data = json.loads((P / "data_4h.json").read_text(encoding="utf-8"))
    out = {}
    for s, rows in data.items():
        keep = [rows[0]] if rows else []
        for a, b in zip(rows, rows[1:]):          # cut at a redenomination / symbol re-use (LUNA, BNX, BTCST, STRAX)
            if a["c"] > 0 and (b["o"] / a["c"] > 3 or b["o"] / a["c"] < 1 / 3 or b["t"] - a["t"] > 3 * DAY):
                break
            keep.append(b)
        out[s] = {r["t"]: r for r in keep}
    return daily, out

def run(daily, bars, depth, tp, days, stop, size, maxpos, s0, s1):
    cash, pos, curve = 170.0, {}, []
    times = sorted({t for d in daily for t in range(d, d + DAY, H4)})
    for t in times:
        d = t // DAY * DAY
        # exits first (bars already open), then new fills on this bar
        for s, p in list(pos.items()):
            r = bars.get(s, {}).get(t)
            if not r:
                continue
            out = None
            if p["stop"] and r["l"] <= p["stop"]:
                out = min(r["o"], p["stop"])
            elif r["h"] >= p["tp"]:
                out = max(r["o"], p["tp"])
            elif t >= p["until"]:
                out = r["c"]
            p["mark"] = r["c"]
            if out is not None:
                cash += p["qty"] * out * (1 - COST); del pos[s]
        eq = cash + sum(p["qty"] * p["mark"] for p in pos.values())
        if s0 <= d < s1:
            for s in daily.get(d, [])[:maxpos]:
                if s in pos:
                    continue
                b = bars.get(s, {}); prev = b.get(d - H4); r = b.get(t)
                if not prev or not r:
                    continue
                # already filled earlier today? then the limit is gone for the day
                if any(b.get(d + k * H4, {}).get("l", 9e99) <= prev["c"] * (1 - depth) for k in range((t - d) // H4)):
                    continue
                limit = prev["c"] * (1 - depth)
                if r["l"] > limit:
                    continue
                px = min(limit, r["o"]) * (1 + COST)
                qty = size * eq / px
                cash -= qty * px
                pos[s] = dict(qty=qty, tp=px * (1 + tp), stop=px * (1 - stop) if stop else None,
                              until=t + days * DAY, mark=r["c"])
                if stop and r["l"] <= px * (1 - stop):      # same bar: assume the stop was hit too (conservative)
                    cash += qty * px * (1 - stop) * (1 - COST); del pos[s]
        curve.append((t + H4, cash + sum(p["qty"] * p["mark"] for p in pos.values())))
    return curve

def dd(c):
    pk, m = c[0][1], 0.0
    for _, e in c:
        pk = max(pk, e); m = max(m, 1 - e / pk)
    return m

VARIANTS = [(0.15, 0.15, 3, 0.25), (0.20, 0.15, 3, 0.25), (0.20, 0.15, 3, None), (0.15, 0.08, 1, 0.25), (0.20, 0.10, 1, 0.25)]
for tag, pdir, starts in (("2021-23", "pit2020", [ts(2021 + (m - 1) // 12, (m - 1) % 12 + 1) for m in range(1, 22)]),
                          ("2023-26", "pit", [ts(2023 + (m - 1) // 12, (m - 1) % 12 + 1) for m in range(9, 33)])):
    daily, bars = load(pdir)
    print(f"\n=== {tag} ===", flush=True)
    for depth, tp, days, stop in VARIANTS:
        for size, maxpos in ((0.10, 10), (0.20, 5), (0.05, 20)):
            c = run(daily, bars, depth, tp, days, stop, size, maxpos, 0, 10**14)
            rets = []
            for s0 in starts:
                w = [x for x in run(daily, bars, depth, tp, days, stop, size, maxpos, s0, s0 + 365 * DAY) if s0 <= x[0] <= s0 + 365 * DAY]
                rets.append(w[-1][1] / 170 - 1 if w else 0)
            sr = sorted(rets)
            print(f"  dip -%{int(depth*100)} kar +%{int(tp*100)} {days}g stop {('-%'+str(int(stop*100))) if stop else 'yok':4} | "
                  f"emir: ilk {maxpos} coin x %{int(size*100)} | tum donem {c[-1][1]/170:6.2f}x dusus %{100*dd(c):3.0f} | "
                  f"12 ay medyan {100*S.median(rets):+5.0f}% en kotu {100*sr[0]:+5.0f}% en iyi {100*sr[-1]:+5.0f}% zararli %{100*sum(x<0 for x in rets)/len(rets):3.0f}",
                  flush=True)
    del bars
print("BITTI")
