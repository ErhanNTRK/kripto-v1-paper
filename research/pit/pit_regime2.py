"""Regime / breadth filters (28 Sep): new ENTRIES only on days when altcoins are trending; exits unchanged.
Run once per dataset (PIT_DIR = pit2020 for Jan 2021-Sep 2023, pit for Sep 2023-Aug 2026). Live rules
(breakout only, trail 4H 6 / 2H 8), risk 1%, REALISTIC costs (fee 0.05%/side, slip 0.02%).
Every indicator for day d uses daily closes up to d-1 only (known at 00:00 UTC of day d).
Gating trick: run2 reads P.daily (the allowed universe per day) at runtime -> on a closed day it gets an empty set."""
import os, sys, math, pickle
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import pit_sim2 as Q
P = Q.P
DAY = P.DAY
P.FEE, P.SLIP = 0.0005, 0.0002
TAG = "2021-23" if "pit2020" in os.environ.get("PIT_DIR", "") else "2023-26"

# daily closes per symbol from 4H bars (a day's close = last 4H close of that UTC day), stamped with the NEXT day
dc = {}
for s, bars in P.BARS["4H"].items():
    m = {}
    for t in sorted(bars):
        if (t + P.H4) % DAY == 0:              # only the LAST 4H bar of a UTC day (no intraday lookahead)
            m[(t + P.H4) // DAY * DAY] = bars[t][3]   # key = day on which this close is known
    dc[s] = m
days = sorted(P.daily)

def ema_series(m, n):
    out, e = {}, None
    for d in sorted(m):
        e = m[d] if e is None else e + (m[d] - e) * 2 / (n + 1)
        out[d] = e
    return out
EMA50 = {s: ema_series(m, 50) for s, m in dc.items()}

# 1) breadth: share of today's universe (ex BTC) with yesterday's close above its daily EMA50
breadth = {}
for d in days:
    u = [s for s in P.daily[d] if s != "BTCUSDT" and d in dc.get(s, {}) and d in EMA50.get(s, {})]
    breadth[d] = sum(dc[s][d] > EMA50[s][d] for s in u) / len(u) if u else 0
# 2) equal-weight alt index (members = the universe of each day) and its ratio to BTC
alt, idx, prev = {}, 1.0, None
for d in days:
    if prev is not None:
        rs = [dc[s][d] / dc[s][prev] - 1 for s in P.daily[prev] if s != "BTCUSDT" and d in dc.get(s, {}) and prev in dc.get(s, {})]
        if rs:
            idx *= 1 + sum(rs) / len(rs)
    alt[d] = idx; prev = d
btc = dc["BTCUSDT"]
ratio = {d: alt[d] / btc[d] for d in days if d in btc}
def above_ema(series, n):
    e = ema_series(series, n)
    return {d: series[d] > e[d] for d in series}
ALT_UP50 = above_ema(alt, 50)
ALT_UP20 = above_ema(alt, 20)
RAT_UP20 = above_ema(ratio, 20)
RAT_UP50 = above_ema(ratio, 50)
eth = {d: dc["ETHUSDT"][d] / btc[d] for d in days if d in dc.get("ETHUSDT", {}) and d in btc}
ETHBTC50 = above_ema(eth, 50)

GATES = {
    "filtre yok (canli)": lambda d: True,
    "genislik >= %40": lambda d: breadth.get(d, 0) >= 0.40,
    "genislik >= %50": lambda d: breadth.get(d, 0) >= 0.50,
    "genislik >= %60": lambda d: breadth.get(d, 0) >= 0.60,
    "alt endeksi > EMA20": lambda d: ALT_UP20.get(d, False),
    "alt endeksi > EMA50": lambda d: ALT_UP50.get(d, False),
    "alt/BTC > EMA20": lambda d: RAT_UP20.get(d, False),
    "alt/BTC > EMA50": lambda d: RAT_UP50.get(d, False),
    "ETH/BTC > EMA50": lambda d: ETHBTC50.get(d, False),
    "genislik>=%50 + alt/BTC>EMA20": lambda d: breadth.get(d, 0) >= 0.50 and RAT_UP20.get(d, False),
    "alt>EMA50 + alt/BTC>EMA50": lambda d: ALT_UP50.get(d, False) and RAT_UP50.get(d, False),
}

from datetime import datetime, timezone
ts = lambda y, m: int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)
if TAG == "2021-23":
    WIN = [(ts(2021, 1), ts(2021, 7)), (ts(2021, 7), ts(2022, 1)), (ts(2022, 1), ts(2023, 1)),
           (ts(2023, 1), ts(2023, 7)), (ts(2023, 7), ts(2023, 10))]
    NM = "Oca-Haz21 Tem-Ara21 2022 Oca-Haz23 Tem-Eyl23".split()
else:
    WIN = [(ts(2023, 9), ts(2024, 3)), (ts(2024, 3), ts(2024, 9)), (ts(2024, 9), ts(2025, 3)),
           (ts(2025, 3), ts(2025, 9)), (ts(2025, 9), ts(2026, 3)), (ts(2026, 3), ts(2026, 9))]
    NM = "Eyl23-Sub24 Mar-Agu24 Eyl24-Sub25 Mar-Agu25 Eyl25-Sub26 Mar-Agu26".split()
def eq_at(c, t, first):
    if first: return next((e for tt, e in c if tt >= t), c[-1][1])
    return next((e for tt, e in reversed(c) if tt <= t), c[0][1])
def rat(c, a, b): return eq_at(c, b, False) / eq_at(c, a, True)
def ddn(c):
    pk, m = None, 0.0
    for _, e in c:
        pk = e if pk is None else max(pk, e); m = max(m, 1 - e / pk)
    return m
def pf(tr):
    w = sum(x[5] for x in tr if x[5] > 0); l = -sum(x[5] for x in tr if x[5] <= 0)
    return w / l if l else 0

open_share = lambda g: sum(g(d) for d in days if P.daily[d]) / max(1, sum(1 for d in days if P.daily[d]))
print(f"=== {TAG} | gercekci maliyet | risk %1 ===")
print(f"{'':32} {'acik gun':>8} {'sonuc':>7} {'PF':>5} {'dusus':>6} {'islem':>5} | " + " ".join(f"{n:>11}" for n in NM), flush=True)
orig = P.daily
res = {}
for name, g in GATES.items():
    for risk in ((0.01, 0.005) if name == "filtre yok (canli)" else (0.01,)):
        P.daily = {d: (u if g(d) else set()) for d, u in orig.items()}
        r = Q.run2(name, momentum=False, trail={"4H": 6.0, "2H": 8.0}, risk=risk)
        P.daily = orig
        c = r["curve"]; res[(name, risk)] = r
        per = [rat(c, a, b) for a, b in WIN]
        lab = name + ("" if risk == 0.01 else " [risk %0.5]")
        print(f"{lab:32} {100*open_share(g):7.0f}% {c[-1][1]/170:6.2f}x {pf(r['trades']):5.2f} %{100*ddn(c):4.0f} {len(r['trades']):5d} | "
              + " ".join(f"{100*(x-1):+10.0f}%" for x in per), flush=True)
pickle.dump({k: {"curve": v["curve"], "trades": v["trades"]} for k, v in res.items()},
            open(Path(__file__).parent / f"pit_regime2_{TAG}.pkl", "wb"))
print("BITTI", flush=True)
