"""BLIND TEST (28 Sep): the same engine on Jan 2021 - Sep 2023 (2021 bull, LUNA/FTX 2022 bear, 2023 recovery),
a period never used in any of our research. Point-in-time daily top-50 (perp listed >=360 days, so the early-2021
universe is small). Current live rules vs old rules vs the single changes; also constant 0.7% risk."""
import os, sys
os.environ["PIT_DIR"] = r"C:\Users\ASUS-PC\kripto\arastirma-verisi\pit2020"
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import pit_sim2 as Q
P = Q.P
from datetime import datetime, timezone
ts = lambda y, m: int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)
WIN = [(ts(2021, 1), ts(2021, 7)), (ts(2021, 7), ts(2022, 1)), (ts(2022, 1), ts(2022, 7)),
       (ts(2022, 7), ts(2023, 1)), (ts(2023, 1), ts(2023, 7)), (ts(2023, 7), ts(2023, 10))]
NM = "Oca-Haz21 Tem-Ara21 Oca-Haz22 Tem-Ara22 Oca-Haz23 Tem-Eyl23".split()
def eq_at(c, t, first):
    if first: return next((e for tt, e in c if tt >= t), c[-1][1])
    return next((e for tt, e in reversed(c) if tt <= t), c[0][1])
def ratio(c, a, b): return eq_at(c, b, False) / eq_at(c, a, True)
def ddn(c):
    pk, m = None, 0.0
    for _, e in c:
        pk = e if pk is None else max(pk, e); m = max(m, 1 - e / pk)
    return m
def pf(tr):
    w = sum(x[5] for x in tr if x[5] > 0); l = -sum(x[5] for x in tr if x[5] <= 0)
    return w / l if l else 0
print(f"{'':40} {'sonuc':>7} {'PF':>5} {'dusus':>6} {'islem':>5} | " + " ".join(f"{n:>10}" for n in NM), flush=True)
T_CUR, T_WIDE = {"4H": 4.0, "2H": 6.0}, {"4H": 6.0, "2H": 8.0}
for label, kw in (("CANLI: momentum kapali + genis iz", dict(momentum=False, trail=T_WIDE)),
                  ("ESKI: momentum acik + simdiki iz", dict(momentum=True, trail=T_CUR)),
                  ("momentum kapali + simdiki iz", dict(momentum=False, trail=T_CUR)),
                  ("momentum acik + genis iz", dict(momentum=True, trail=T_WIDE)),
                  ("CANLI ama risk %0.7", dict(momentum=False, trail=T_WIDE, risk=0.007)),
                  ("CANLI ama risk %0.5", dict(momentum=False, trail=T_WIDE, risk=0.005))):
    r = Q.run2(label, **kw); c = r["curve"]
    per = [ratio(c, a, b) for a, b in WIN]
    print(f"{label:40} {c[-1][1]/170:6.2f}x {pf(r['trades']):5.2f} %{100*ddn(c):4.0f} {len(r['trades']):5d} | "
          + " ".join(f"{100*(x-1):+9.0f}%" for x in per), flush=True)
# BTC buy-and-hold reference
b = P.BARS["4H"]["BTCUSDT"]; bt = sorted(t for t in b if WIN[0][0] <= t < WIN[-1][1])
print(f"\nBTC al-tut: {b[bt[-1]][3]/b[bt[0]][0]:.2f}x | " + " ".join(
    f"{100*(b[max(t for t in bt if t < bb)][3]/b[min(t for t in bt if t >= aa)][0]-1):+9.0f}%" for aa, bb in WIN))
print("BITTI", flush=True)
