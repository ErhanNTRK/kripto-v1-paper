"""The user's objection (29 Sep, AVAX): 'don't buy a breakout just under a recent spike top where people sell'.
For every live-rule PIT trade: distance from the entry to the highest high of the last 30 days (excluding the
signal bar), and whether that top came from a spike. Quintile-free buckets, both datasets, 2H and 4H."""
import json, pickle, statistics as S
from pathlib import Path
DAY = 86_400_000
IV = {"4H": 4 * 3600 * 1000, "2H": 2 * 3600 * 1000}
def stats(g):
    if not g: return "   yok"
    w = sum(x["pnl"] for x in g if x["pnl"] > 0); l = -sum(x["pnl"] for x in g if x["pnl"] <= 0)
    return f"n {len(g):4d} ortR {S.mean(x['R'] for x in g):+.2f} PF {w/l if l else 0:4.2f} 5R+ {sum(x['R']>=5 for x in g):3d} kazanan %{100*sum(x['R']>0 for x in g)/len(g):.0f}"
for tag, pdir in (("2021-23", "pit2020"), ("2023-26", "pit")):
    tr = pickle.load(open(f"pit_regime2_{tag}.pkl", "rb"))[("filtre yok (canli)", 0.01)]["trades"]
    feats = []
    for sn, fl in (("4H", "4h"), ("2H", "2h")):
        data = json.loads(Path(rf"C:\Users\ASUS-PC\kripto\arastirma-verisi\{pdir}\data_{fl}.json").read_text(encoding="utf-8"))
        idx = {s: {r["t"]: i for i, r in enumerate(rows)} for s, rows in data.items()}
        n30 = 30 * DAY // IV[sn]
        for x in tr:
            if x[0] != sn or x[1] not in data: continue
            i = idx[x[1]].get(x[3] - IV[sn])          # signal bar
            if i is None or i < n30: continue
            rows = data[x[1]]; c = rows[i]["c"]
            top = max(r["h"] for r in rows[i - n30:i])
            feats.append(dict(sn=sn, R=x[2], pnl=x[5], gap=top / c - 1))
        del data, idx
    print(f"\n=== {tag}: son 30 gunun zirvesine uzaklik (girise gore) ===")
    for name, lo, hi in (("zirvenin USTUNDE (yeni zirve kirilimi)", -9, 0), ("zirveye %0-3 kala", 0, 0.03),
                         ("zirveye %3-8 kala", 0.03, 0.08), ("zirveye %8-15 kala", 0.08, 0.15), ("zirveye %15+ kala", 0.15, 9)):
        print(f"  {name:40} {stats([f for f in feats if lo <= f['gap'] < hi])}")
print("BITTI")
