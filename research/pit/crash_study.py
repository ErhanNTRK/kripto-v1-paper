"""The user's idea (29 Sep): catch flash-crash wicks like AAVE's 10 Oct 2025 drop to 80 and the rebound.
Mechanism tested: every day, for every coin in that day's top-50, a resting LIMIT BUY at (yesterday's daily close) x
(1 - depth). Filled on the first 4H bar whose low touches it (at the limit, or at the bar open if it opened below).
Exits compared: (a) the close of the 4H bar 24h after the fill, (b) +15% take-profit else close after 3 days,
(c) close after 7 days; each also with a -25% stop from the fill. Costs 0.07% per side. Spot 4H bars, both eras.
Output: per depth / exit -> fills, distinct crash days, mean and median return, win rate, worst, and the share of
profit coming from the single best day (is it all one event?)."""
import json, statistics as S
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
DAY, H4 = 86_400_000, 4 * 3600 * 1000
COST = 0.0007
fmt = lambda t: datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m-%d")

for tag, pdir in (("2021-23", "pit2020"), ("2023-26", "pit")):
    P = Path(rf"C:\Users\ASUS-PC\kripto\arastirma-verisi\{pdir}")
    daily = {int(k): [s for s in v if s != "BTCUSDT"] for k, v in json.loads((P / "daily_top50.json").read_text()).items()}
    data = json.loads((P / "data_4h.json").read_text(encoding="utf-8"))
    bars = {s: {r["t"]: r for r in rows} for s, rows in data.items()}
    del data
    print(f"\n=== {tag} ===", flush=True)
    for depth in (0.15, 0.20, 0.30, 0.40):
        fills = []
        for d, syms in daily.items():
            for s in syms:
                b = bars.get(s, {})
                prev = b.get(d - H4)                      # last 4H bar of yesterday -> yesterday's close
                if not prev:
                    continue
                limit = prev["c"] * (1 - depth)
                for k in range(6):                         # today's six 4H bars
                    r = b.get(d + k * H4)
                    if r and r["l"] <= limit:
                        fills.append((s, d + k * H4, min(limit, r["o"]))); break
        res = defaultdict(list)
        for s, t, px in fills:
            b = bars[s]; entry = px * (1 + COST)
            later = [b[t + j * H4] for j in range(1, 43) if (t + j * H4) in b]   # up to 7 days
            if not later:
                continue
            first = b[t]
            for stop in (None, 0.25):
                sp = entry * (1 - stop) if stop else None
                def run(n, tp=None):
                    # the fill bar itself: its close after the fill is known only as the bar close
                    for r in [first] + later[:n]:
                        if sp and r["l"] <= sp:
                            return min(r["o"], sp) if r is not first else sp
                        # no take-profit on the fill bar: its high most likely came BEFORE the drop
                        if tp and r is not first and r["h"] >= entry * (1 + tp):
                            return max(r["o"], entry * (1 + tp))
                    return (later[:n] or [first])[-1]["c"]
                for name, n, tp in (("24 saat sonra sat", 6, None), ("+%15 kar al / 3 gun", 18, 0.15), ("7 gun tut", 42, None)):
                    out = run(n, tp) * (1 - COST)
                    res[(name, stop)].append((out / entry - 1, t // DAY * DAY, s))
        for (name, stop), rs in res.items():
            rets = [x[0] for x in rs]
            byday = defaultdict(float)
            for r, dd, _ in rs:
                byday[dd] += r
            best_day = max(byday, key=byday.get) if byday else None
            tot = sum(rets)
            share = byday[best_day] / tot if best_day and tot > 0 else float("nan")
            print(f"  dip -%{int(depth*100)} | {name:20} stop {'-%25' if stop else 'yok ':4} | dolum {len(rets):4d} ({len(byday):3d} gun) | "
                  f"ort {100*S.mean(rets):+6.1f}% medyan {100*S.median(rets):+6.1f}% | kazanan %{100*sum(r>0 for r in rets)/len(rets):3.0f} | "
                  f"en kotu {100*min(rets):+6.1f}% | en iyi gun {fmt(best_day)} (karin %{100*share:.0f}'i)", flush=True)
    del bars
print("BITTI")
