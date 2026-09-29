"""Robustness of the new live rules (111c1de: breakout only, trail 4H 6 / 2H 8) on the point-in-time universe:
neighbouring trail distances, and double slippage. Reuses pit_sim2.run2."""
import sys, pickle
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import pit_sim2 as Q
P = Q.P
from datetime import datetime, timezone
ts = lambda y, m: int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)
WIN = [(ts(2023, 9), ts(2024, 3)), (ts(2024, 3), ts(2024, 9)), (ts(2024, 9), ts(2025, 3)),
       (ts(2025, 3), ts(2025, 9)), (ts(2025, 9), ts(2026, 3)), (ts(2026, 3), ts(2026, 9))]
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
def show(label, r):
    c = r["curve"]; per = [ratio(c, a, b) for a, b in WIN]
    print(f"{label:34} {c[-1][1]/170:6.1f}x PF {pf(r['trades']):4.2f} dusus %{100*ddn(c):3.0f} | "
          + " ".join(f"{100*(x-1):+5.0f}%" for x in per) + f" | son12 {100*(per[4]*per[5]-1):+4.0f}%", flush=True)
res = {}
print("=== momentum KAPALI, iz mesafesi komsulari (4H x 2H) ===")
for a in (4, 5, 6, 7, 8):
    for b in (6, 7, 8, 9, 10):
        k = f"kirilim iz 4H {a} / 2H {b}" + ("  <- CANLI" if (a, b) == (6, 8) else "")
        res[k] = Q.run2(k, momentum=False, trail={"4H": float(a), "2H": float(b)}); show(k, res[k])
print("\n=== referans ===")
k = "ESKI kurallar (mom acik, 4/6)"; res[k] = Q.run2(k, momentum=True, trail={"4H": 4.0, "2H": 6.0}); show(k, res[k])
print("\n=== kayma 2 kat ===")
s0 = P.SLIP; P.SLIP = 2 * s0
k = "CANLI, kayma 2x"; res[k] = Q.run2(k, momentum=False, trail={"4H": 6.0, "2H": 8.0}); show(k, res[k])
k = "ESKI, kayma 2x"; res[k] = Q.run2(k, momentum=True, trail={"4H": 4.0, "2H": 6.0}); show(k, res[k])
P.SLIP = s0
print("\n=== risk %0.75 (canli kurallar) ===")
k = "CANLI risk 0.75%"; res[k] = Q.run2(k, momentum=False, trail={"4H": 6.0, "2H": 8.0}, risk=0.0075); show(k, res[k])
pickle.dump(res, open(Path(__file__).with_suffix(".pkl"), "wb"))
print("BITTI", flush=True)
