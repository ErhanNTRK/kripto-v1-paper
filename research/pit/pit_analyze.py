# Reads pit_sim.pkl and prints the point-in-time / walk-forward report (Turkish labels).
import json, pickle
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

SP = Path(__file__).parent
R = pickle.load(open(SP / "pit_sim.pkl", "rb"))
PIT = Path(r"C:\Users\ASUS-PC\kripto\arastirma-verisi\pit")
daily = {int(k): set(v) for k, v in json.loads((PIT / "daily_top50.json").read_text()).items()}
last_list = daily[max(daily)]
ts = lambda y, m: int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)
WIN = [(ts(2023, 9), ts(2024, 3)), (ts(2024, 3), ts(2024, 9)), (ts(2024, 9), ts(2025, 3)),
       (ts(2025, 3), ts(2025, 9)), (ts(2025, 9), ts(2026, 3)), (ts(2026, 3), ts(2026, 9))]
names = ["Eyl23-Sub24", "Mar24-Agu24", "Eyl24-Sub25", "Mar25-Agu25", "Eyl25-Sub26", "Mar26-Agu26"]
CUR = "ichimoku acik, momentum acik, iz simdiki"
CTL = [k for k in R if k.startswith("KONTROL")][0]


def eq_at(curve, t, first=True):
    if first:
        return next((e for tt, e in curve if tt >= t), curve[-1][1])
    return next((e for tt, e in reversed(curve) if tt <= t), curve[0][1])


def ratio(curve, a, b):
    return eq_at(curve, b, False) / eq_at(curve, a, True)


def dd(curve, a=0, b=10**15):
    pk, m = None, 0.0
    for t, e in curve:
        if a <= t <= b:
            pk = e if pk is None else max(pk, e)
            m = max(m, 1 - e / pk)
    return m


def pf(trades):
    w = sum(x[5] for x in trades if x[5] > 0); l = -sum(x[5] for x in trades if x[5] <= 0)
    return w / l if l else float("inf")


print("=== 1) TUM DONEM (Eyl 2023 - Agu 2026), 170 USDT ===")
for k in [CTL, CUR] + [k for k in R if k not in (CTL, CUR)]:
    c, tr = R[k]["curve"], R[k]["trades"]
    rs = [x[2] for x in tr]
    print(f"{k:50} {c[-1][1]/170:7.1f}x | dusus %{100*dd(c):.0f} | islem {len(tr)} | ort {sum(rs)/len(rs):+.2f}R | PF {pf(tr):.2f} | "
          f"kazanan %{100*sum(r>0 for r in rs)/len(rs):.0f}")

print("\n=== 2) 6 AYLIK DONEMLER (donem getirisi) ===")
print(f"{'':50} " + " ".join(f"{n:>11}" for n in names))
for k in [CTL, CUR]:
    c = R[k]["curve"]
    print(f"{k:50} " + " ".join(f"{100*(ratio(c, a, b)-1):+10.0f}%" for a, b in WIN))

print("\n=== 3) WALK-FORWARD: her 6 ayda son 12 aya gore en iyi kombinasyon, sonraki 6 ayda uygulanir ===")
combos = [k for k in R if k != CTL]
wf, cur_only = 1.0, 1.0
for (a, b), n in zip(WIN[2:], names[2:]):
    look = (a - 365 * 86_400_000, a)
    best = max(combos, key=lambda k: ratio(R[k]["curve"], *look))
    r_best = ratio(R[best]["curve"], a, b); r_cur = ratio(R[CUR]["curve"], a, b)
    wf *= r_best; cur_only *= r_cur
    print(f"{n}: secilen [{best}] -> bu donem {100*(r_best-1):+.0f}% | simdiki kurallar {100*(r_cur-1):+.0f}%")
print(f"Eyl 2024 - Agu 2026 toplam (2 yil, hic bakilmamis donemler): walk-forward {100*(wf-1):+.0f}% | simdiki kurallar sabit {100*(cur_only-1):+.0f}%")

print("\n=== 4) KAR NEREDEN GELIYOR? (gunluk gercek top-50, simdiki kurallar) ===")
tr = R[CUR]["trades"]
byc = defaultdict(float); byu = defaultdict(float)
for sn, s, r, tin, tout, usd in tr:
    byc[s] += r; byu[s] += usd
tot = sum(byc.values())
rk = sorted(byc.items(), key=lambda x: -x[1])
cum = 0
for i, (s, r) in enumerate(rk[:10], 1):
    cum += r
    print(f"{i:2}. {s:14} {r:+6.1f}R  toplamin %{100*r/tot:.0f}  (kumulatif %{100*cum/tot:.0f})  {'bugun listede' if s in last_list else 'BUGUN LISTEDE DEGIL'}")
gone_r = sum(r for s, r in byc.items() if s not in last_list)
print(f"toplam {tot:.0f}R, {len(byc)} coin; bugun listede olmayan coinlerden gelen {gone_r:+.0f}R (%{100*gone_r/tot:.0f})")
print(f"zarar ettiren coin: {sum(1 for r in byc.values() if r < 0)} / {len(byc)}")
big = sorted((x for x in tr), key=lambda x: -x[2])[:10]
print("en buyuk 10 islem:", [(x[1], round(x[2], 1)) for x in big])
