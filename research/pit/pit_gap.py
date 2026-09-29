"""Multi-start evaluation (29 Sep): instead of ONE path per idea, run many 12-month windows that start on the 1st of
every month, each from a fresh 170 USDT, and look at the distribution. Entries are allowed only inside the window
(P.daily gating); equity is read at the window end, drawdown inside the window. Realistic costs.
Run once per dataset: PIT_DIR=...pit2020 (starts Jan 2021..Sep 2022) and PIT_DIR=...pit (starts Sep 2023..Aug 2025)."""
import os, sys, json, statistics as S
from pathlib import Path
from datetime import datetime, timezone
sys.path.insert(0, str(Path(__file__).parent))
import pit_sim2 as Q
P = Q.P
DAY = P.DAY
P.FEE, P.SLIP = 0.0005, 0.0002
TAG = "2021-23" if "pit2020" in os.environ.get("PIT_DIR", "") else "2023-26"

# ETH/BTC daily closes, known the next day (last 4H bar of a UTC day only -> no intraday lookahead)
def daily(sym):
    m = {}
    for t in sorted(P.BARS["4H"][sym]):
        if (t + P.H4) % DAY == 0:
            m[(t + P.H4) // DAY * DAY] = P.BARS["4H"][sym][t][3]
    return m
eb, bb = daily("ETHUSDT"), daily("BTCUSDT")
eth = {d: eb[d] / bb[d] for d in eb if d in bb}
def above_ema(series, n):
    out, e = {}, None
    for d in sorted(series):
        e = series[d] if e is None else e + (series[d] - e) * 2 / (n + 1)
        out[d] = series[d] > e
    return out
ks = sorted(eth)
R30 = {ks[i]: eth[ks[i]] > eth[ks[i - 30]] for i in range(30, len(ks))}
G = {"yok": lambda d: True, "ETH/BTC>EMA30": None, "ETH/BTC>EMA50": None, "ETH/BTC>EMA75": None,
     "ETH/BTC 30g>0": lambda d: R30.get(d, False)}
for n in (30, 50, 75):
    ab = above_ema(eth, n); G[f"ETH/BTC>EMA{n}"] = (lambda ab: lambda d: ab.get(d, False))(ab)

CONFIGS = [(g, 0.01, cap) for g in ("yok", "ETH/BTC>EMA50") for cap in (None, 0.10, 0.15, 0.20)]

ts = lambda y, m: int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)
if TAG == "2021-23":
    starts = [ts(2021 + (m - 1) // 12, (m - 1) % 12 + 1) for m in range(1, 22)]        # Jan 2021 .. Sep 2022
else:
    starts = [ts(2023 + (m - 1) // 12, (m - 1) % 12 + 1) for m in range(9, 33)]        # Sep 2023 .. Aug 2025
YEAR = 365 * DAY
orig = P.daily
out = {}
IVS = {"4H": P.H4, "2H": P.H2}
ORIG_ENT = {sn: {k: dict(v) for k, v in P.ENT[sn].items()} for sn in ("4H", "2H")}
def gap_ok(sn, sym, t, cap):
    bars = P.BARS[sn].get(sym, {}); n = 30 * DAY // IVS[sn]
    hs = [bars[t - j * IVS[sn]][1] for j in range(1, n + 1) if (t - j * IVS[sn]) in bars]
    return not hs or bars[t][3] <= 0 or max(hs) / bars[t][3] - 1 < cap
FILTERED = {}
def ent_for(cap):
    if cap is None:
        return ORIG_ENT
    if cap not in FILTERED:
        FILTERED[cap] = {sn: {k: {t: [x for x in lst if gap_ok(sn, x[0], t, cap)] for t, lst in v.items()}
                              for k, v in ORIG_ENT[sn].items()} for sn in ("4H", "2H")}
    return FILTERED[cap]
ORDERED = {int(k): [x for x in v if x != "BTCUSDT"] for k, v in json.loads((P.PIT / "daily_top50.json").read_text()).items()}
print(f"=== {TAG}: {len(starts)} adet 12 aylik pencere, her biri 170 USDT ile ===", flush=True)
for g, risk, cap in CONFIGS:
    for sn in ("4H", "2H"):
        P.ENT[sn] = ent_for(cap)[sn]
    rets, dds = [], []
    for s0 in starts:
        s1 = s0 + YEAR
        P.daily = {d: (u if s0 <= d < s1 and G[g](d) else set()) for d, u in orig.items()}
        r = Q.run2("x", momentum=False, trail={"4H": 6.0, "2H": 8.0}, risk=risk)
        c = [(t, e) for t, e in r["curve"] if s0 <= t <= s1]
        pk, dd = c[0][1], 0.0
        for _, e in c:
            pk = max(pk, e); dd = max(dd, 1 - e / pk)
        rets.append(c[-1][1] / 170 - 1); dds.append(dd)
    P.daily = orig
    out[f"{g} {cap}"] = {"rets": rets, "dds": dds}
    sr = sorted(rets)
    print(f"{g:15} zirveye uzaklik < {cap} | 12 ay getiri medyan {100*S.median(rets):+6.0f}% | kotu %10 {100*sr[len(sr)//10]:+5.0f}% | "
          f"en kotu {100*sr[0]:+5.0f}% | en iyi {100*sr[-1]:+6.0f}% | zararli pencere %{100*sum(x<0 for x in rets)/len(rets):3.0f} | "
          f"dusus medyan %{100*S.median(dds):3.0f} en kotu %{100*max(dds):3.0f}", flush=True)
json.dump({"starts": starts, "res": out}, open(Path(__file__).parent / f"pit_gap_{TAG}.json", "w"))
print("BITTI", flush=True)
