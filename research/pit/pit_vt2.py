"""Follow-up on volatility targeting: plateau check (targets 25-45%, lookbacks 14/30/60) and a control with a
CONSTANT risk equal to the average multiplier actually applied, so we can tell timing from simply lower risk."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import pit_ideas as I
P = I.P
for lb in (14, 60):
    I.VOL[lb] = I.vol_table(lb)
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
          + " ".join(f"{100*(x-1):+5.0f}%" for x in per)
          + f" | son12 {100*(per[4]*per[5]-1):+4.0f}% | en kotu {100*(min(per)-1):+4.0f}%", flush=True)
def avg_mult(r, target, lb):
    ms = [min(1.0, target / I.VOL[lb][x[3] // I.DAY * I.DAY]) for x in r["trades"] if I.VOL[lb].get(x[3] // I.DAY * I.DAY)]
    return sum(ms) / len(ms)
show("CANLI (referans)", I.run3())
for lb in (14, 30, 60):
    for target in (0.25, 0.30, 0.35, 0.40, 0.45):
        r = I.run3(vt=(target, lb, 1.0))
        m = avg_mult(r, target, lb)
        show(f"vol %{int(target*100)} {lb}g (ort carpan {m:.2f})", r)
        if lb == 30:
            show(f"   kontrol: sabit risk %{m:.2f}", I.run3(risk=0.01 * m))
print("BITTI", flush=True)
