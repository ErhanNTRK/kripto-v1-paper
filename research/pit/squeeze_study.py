"""The user's idea #1 (28 Sep): the most compressed ("sıkışan") coins rise the most when BTC is rising.
Features at the SIGNAL bar (last closed bar before entry) of every live-rule PIT trade (pit_robust.pkl):
  bbw_rank - Bollinger(20,2) width percentile among its own last 120 bars (0 = tightest ever in that window)
  base20   - range of the 20 bars before the signal bar / close (tight base = small)
  atrp     - ATR(14) / close
  btc7     - BTC return over the last 7 days on the same timeframe
Quintile cuts from Sep 2023-Aug 2025, checked on Sep 2025-Aug 2026. Then compression x BTC strength (2x2)."""
import json, math, pickle, statistics as S
from datetime import datetime, timezone
from pathlib import Path
HERE = Path(__file__).parent
PIT = Path(r"C:\Users\ASUS-PC\kripto\arastirma-verisi\pit")
res = pickle.load(open(HERE / "pit_robust.pkl", "rb"))
key = next(k for k in res if "CANLI" in k and "kayma" not in k and "risk" not in k)
trades = res[key]["trades"]
IV = {"4H": 4 * 3600 * 1000, "2H": 2 * 3600 * 1000}
split = int(datetime(2025, 9, 1, tzinfo=timezone.utc).timestamp() * 1000)
feat = []
for sn, fl in (("4H", "4h"), ("2H", "2h")):
    data = json.loads((PIT / f"data_{fl}.json").read_text(encoding="utf-8"))
    idx = {s: {r["t"]: i for i, r in enumerate(rows)} for s, rows in data.items()}
    btc = data["BTCUSDT"]; bidx = idx["BTCUSDT"]
    look7 = 7 * 24 * 3600 * 1000 // IV[sn]
    for tsn, s, R, t_in, t_out, pnl, kind in trades:
        if tsn != sn or s not in data:
            continue
        sig = t_in - IV[sn]
        i = idx[s].get(sig); j = bidx.get(sig)
        if i is None or j is None or i < 140 or j < look7:
            continue
        rows = data[s]
        c = [r["c"] for r in rows[i - 139:i + 1]]
        widths = []
        for k in range(19, len(c)):
            w = c[k - 19:k + 1]; m = sum(w) / 20
            sd = math.sqrt(sum((x - m) ** 2 for x in w) / 20)
            widths.append(4 * sd / m if m else 0)
        last = widths[-120:]
        bbw_rank = sum(x < last[-1] for x in last) / len(last)
        base = rows[i - 20:i]
        base20 = (max(r["h"] for r in base) - min(r["l"] for r in base)) / rows[i]["c"]
        trs = [max(rows[k]["h"] - rows[k]["l"], abs(rows[k]["h"] - rows[k - 1]["c"]), abs(rows[k]["l"] - rows[k - 1]["c"])) for k in range(i - 13, i + 1)]
        atrp = sum(trs) / 14 / rows[i]["c"]
        btc7 = btc[j]["c"] / btc[j - look7]["c"] - 1
        feat.append(dict(sn=sn, s=s, R=R, pnl=pnl, t=t_in, bbw_rank=bbw_rank, base20=base20, atrp=atrp, btc7=btc7))
    del data, idx
print(f"{len(feat)} / {len(trades)} islem icin ozellik hesaplandi", flush=True)
json.dump(feat, open(HERE / "squeeze_features.json", "w"))


def stats(g):
    if not g:
        return f"{0:>5} {'':>6} {'':>5} {'':>4}"
    w = sum(x["pnl"] for x in g if x["pnl"] > 0); l = -sum(x["pnl"] for x in g if x["pnl"] <= 0)
    return f"{len(g):>5} {S.mean(x['R'] for x in g):+6.2f} {w/l if l else 0:5.2f} {sum(x['R']>=5 for x in g):>4}"


def table(k, label):
    disc = sorted(x[k] for x in feat if x["t"] < split)
    cuts = [disc[int(len(disc) * q / 5)] for q in (1, 2, 3, 4)]
    b = lambda v: sum(v >= c for c in cuts)
    print(f"\n{label} [{k}]  sinirlar: {', '.join(f'{c:.3g}' for c in cuts)}")
    print(f"dilim |  kesif: n  ortR    PF  5R+ | dogrulama: n  ortR    PF  5R+")
    for q in range(5):
        print(f"{q+1:>5} | {stats([x for x in feat if x['t'] < split and b(x[k]) == q])} | "
              f"{stats([x for x in feat if x['t'] >= split and b(x[k]) == q])}")


table("bbw_rank", "Bollinger genisligi sirasi (1 = en sikisik)")
table("base20", "20 mumluk taban genisligi (1 = en dar)")
table("atrp", "ATR% (1 = en sakin coin)")
table("btc7", "BTC son 7 gun (1 = en zayif)")
print("\n=== SIKISMA x BTC GUCU (sinirlar kesif donemi medyanlari) ===")
for k in ("bbw_rank", "base20"):
    mk = S.median(x[k] for x in feat if x["t"] < split); mb = S.median(x["btc7"] for x in feat if x["t"] < split)
    print(f"\n{k} (sikisik < {mk:.3g}) x BTC 7g (guclu > {mb:+.3f})")
    for comp in (True, False):
        for strong in (True, False):
            g = lambda part: [x for x in feat if part(x["t"]) and (x[k] < mk) == comp and (x["btc7"] > mb) == strong]
            print(f"  {'sikisik' if comp else 'genis  '} + BTC {'guclu' if strong else 'zayif'} | kesif {stats(g(lambda t: t < split))} | dogrulama {stats(g(lambda t: t >= split))}")
print("BITTI", flush=True)
