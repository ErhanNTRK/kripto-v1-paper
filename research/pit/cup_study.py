"""The user's idea #2 (28 Sep): after a strong rise, profit-taking pulls the coin back, then a rounded "cup"
(çanak) brings it back toward the old high. Event study on the point-in-time universe (daily bars built from 4H):
  surge : on peak day p, close_p is the 60-day closing high AND >= +40% above the 30-day low before it
  pullback: afterwards the low falls 20-60% below the peak close (trough T), no close above the peak meanwhile
  cup   : day r = first close back above T + 50% of (peak - T); trough at least 3 days after the peak;
          'rounded' if r is >= 5 days after the trough, 'V' otherwise; r within 90 days of the peak
Trade at close of r: stop = trough low, target = peak close, max 30 days. Outcome in R, plus 'did it reach the old
high', vs a baseline of every coin-day in the universe. Only data up to day r is used to define the event.
Discovery Sep 2023-Aug 2025, validation Sep 2025-Aug 2026."""
import json, statistics as S
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
PIT = Path(r"C:\Users\ASUS-PC\kripto\arastirma-verisi\pit")
DAY = 86_400_000
daily_top = {int(k): set(v) for k, v in json.loads((PIT / "daily_top50.json").read_text()).items()}
raw = json.loads((PIT / "data_4h.json").read_text(encoding="utf-8"))
split = int(datetime(2025, 9, 1, tzinfo=timezone.utc).timestamp() * 1000)
FEE_R = 0.0  # costs added below in price terms
COST = 0.002  # round trip fee+slippage as a fraction of price


def to_daily(rows):
    g = defaultdict(list)
    for r in rows:
        g[r["t"] // DAY * DAY].append(r)
    out = []
    for d in sorted(g):
        b = g[d]
        if len(b) < 6:
            continue
        out.append(dict(t=d, o=b[0]["o"], h=max(x["h"] for x in b), l=min(x["l"] for x in b), c=b[-1]["c"]))
    return out


D = {s: to_daily(rows) for s, rows in raw.items()}
del raw
btc = {r["t"]: r["c"] for r in D["BTCUSDT"]}
bt = sorted(btc); ema = {}; e = None
for t in bt:
    e = btc[t] if e is None else e + (btc[t] - e) * 2 / 51
    ema[t] = e

events = []
base = []   # baseline: 30-day forward return of every in-universe coin-day
for s, rows in D.items():
    if s == "BTCUSDT":
        continue
    n = len(rows)
    for i in range(n - 30):
        if rows[i]["t"] in daily_top and s in daily_top[rows[i]["t"]] and i % 5 == 0:
            base.append((rows[i]["t"], rows[i + 30]["c"] / rows[i]["c"] - 1))
    p = 60
    while p < n - 1:
        cp = rows[p]["c"]
        if cp < max(r["c"] for r in rows[p - 60:p]) or cp < 1.4 * min(r["l"] for r in rows[p - 30:p]):
            p += 1; continue
        # walk forward from the peak: track trough, stop if a close exceeds the peak or 90 days pass
        trough, ti, found = None, None, None
        for k in range(p + 1, min(n, p + 91)):
            if rows[k]["c"] > cp:
                break
            if trough is None or rows[k]["l"] < trough:
                trough, ti = rows[k]["l"], k
            depth = 1 - trough / cp
            if 0.20 <= depth <= 0.60 and ti >= p + 3 and k > ti and rows[k]["c"] >= trough + 0.5 * (cp - trough):
                found = k; break
        if found is None:
            p += 1; continue
        r = found; t = rows[r]["t"]
        if t in daily_top and s in daily_top[t] and r + 1 < n:
            entry = rows[r]["c"] * (1 + COST / 2); stop = trough; risk = entry - stop
            outcome, hit_peak, days = None, False, 0
            for k in range(r + 1, min(n, r + 31)):
                days = k - r
                if rows[k]["l"] <= stop:
                    outcome = (min(rows[k]["o"], stop) * (1 - COST / 2) - entry) / risk; break
                if rows[k]["h"] >= cp:
                    outcome = (max(rows[k]["o"], cp) * (1 - COST / 2) - entry) / risk; hit_peak = True; break
            if outcome is None:
                last = rows[min(n - 1, r + 30)]["c"]
                outcome = (last * (1 - COST / 2) - entry) / risk
            ret30 = rows[min(n - 1, r + 30)]["c"] / rows[r]["c"] - 1
            bt_t = max((x for x in bt if x <= t), default=None)
            events.append(dict(s=s, t=t, R=outcome, hit=hit_peak, ret30=ret30, days=days,
                               rounded=(r - ti) >= 5, depth=1 - trough / cp,
                               btc_up=bool(bt_t and btc[bt_t] > ema[bt_t]),
                               reward_risk=(cp - entry) / risk))
        p = r + 1   # next event only after this one

print(f"olay sayisi {len(events)}, taban gun {len(base)}")


def show(label, g):
    if not g:
        print(f"{label:40} yok"); return
    wins = [x["R"] for x in g if x["R"] > 0]; loss = [-x["R"] for x in g if x["R"] <= 0]
    print(f"{label:40} n {len(g):4d} | eski zirveye donus %{100*sum(x['hit'] for x in g)/len(g):3.0f} | "
          f"ort R {S.mean(x['R'] for x in g):+.2f} | PF(R) {sum(wins)/sum(loss) if loss else 0:4.2f} | "
          f"30g getiri medyan {100*S.median(x['ret30'] for x in g):+5.1f}% | odul/risk medyan {S.median(x['reward_risk'] for x in g):.1f}")


for part, name in ((lambda t: t < split, "KESIF Eyl23-Agu25"), (lambda t: t >= split, "DOGRULAMA Eyl25-Agu26")):
    print(f"\n=== {name} ===")
    b = [r for t, r in base if part(t)]
    print(f"taban: rastgele coin-gun 30g getiri medyan {100*S.median(b):+.1f}%  ort {100*S.mean(b):+.1f}%")
    ev = [x for x in events if part(x["t"])]
    show("tum canak olaylari", ev)
    show("  yuvarlak (dip sonrasi >=5 gun)", [x for x in ev if x["rounded"]])
    show("  V seklinde (<5 gun)", [x for x in ev if not x["rounded"]])
    show("  BTC EMA50 ustunde", [x for x in ev if x["btc_up"]])
    show("  BTC EMA50 altinda", [x for x in ev if not x["btc_up"]])
    show("  yuvarlak + BTC ustunde", [x for x in ev if x["rounded"] and x["btc_up"]])
    show("  derinlik %20-35", [x for x in ev if x["depth"] < 0.35])
    show("  derinlik %35-60", [x for x in ev if x["depth"] >= 0.35])
print("BITTI", flush=True)
