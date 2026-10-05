"""Two questions from the user (5 Oct 2026), on 4h spot bars of the point-in-time top 50, Jan 2021 - Aug 2026
(~/kripto/arastirma-verisi/pit2020 + pit), read as daily closes:

1. A mania rule for the pump fade (short the 10 top-50 coins that rose most over 30 days, a week, stop +40%, while
   ETH/BTC is above its 50-day EMA): it won in 2022 and 2024-26 but died in the 2021 alt mania. Instead of guessing a
   rule, every fade week is tagged with candidate mania gauges known on its Monday, and the weeks are bucketed:
     med30   median 30-day return of the top 50
     hot     share of the top 50 up more than 50% in 30 days
     btc90   BTC's 90-day return
     tenth   the 30-day rise of the 10th pick (how hot even the "least hot" short is)
2. Is the pair (long BTC with half, short an equal basket of the top-N alts with the other half, rebalanced every
   Monday) usable? Basket sizes 10 / 20 / 49, run always / gate closed / gate open, costs 0.10% a side on what is
   traded at the weekly rebalance (~30% of the book) and funding 0 or 0.03% a day paid on both legs."""
import json, math, statistics as S
from datetime import datetime, timezone
from pathlib import Path

DAY = 86_400_000
FEE = 0.001


def load(era):
    P = Path.home() / "kripto" / "arastirma-verisi" / era
    raw = json.loads((P / "data_4h.json").read_text(encoding="utf-8"))
    top = {int(k) // DAY: v for k, v in json.loads((P / "daily_top50.json").read_text()).items()}
    close, high = {}, {}
    for s, rows in raw.items():
        c, h, last = {}, {}, None
        for r in rows:
            if last and (r["o"] / last > 3 or r["o"] / last < 1 / 3): break
            d = r["t"] // DAY
            c[d] = r["c"]; h[d] = max(h.get(d, r["h"]), r["h"]); last = r["c"]
        close[s], high[s] = c, h
    return close, high, top


def merged():
    a, b = load("pit2020"), load("pit")
    close, high, top = {}, {}, {}
    for c, h, t in (a, b):
        for s in c:
            close.setdefault(s, {}).update(c[s]); high.setdefault(s, {}).update(h[s])
        top.update(t)
    return close, high, top


def main():
    close, high, top = merged()
    days = sorted(close["BTCUSDT"])
    rd = [d for d in days if d in close["ETHUSDT"]]
    ratio = [close["ETHUSDT"][d] / close["BTCUSDT"][d] for d in rd]
    gate, m = {}, None
    for d, r in zip(rd, ratio):
        m = r if m is None else m + (r - m) * 2 / 51; gate[d] = r > m
    first = min(top) + 95
    mondays = [d for d in days if d >= first and d + 6 <= days[-1]
               and datetime.fromtimestamp(d * DAY / 1000, timezone.utc).weekday() == 0]

    def univ(prev, end, lb=30):
        return [s for s in top.get(prev, top.get(prev + 1, [])) if s != "BTCUSDT"
                and prev in close.get(s, {}) and prev - lb in close[s] and end in close[s]]

    # ---------- 1. mania gauges vs the fade ----------
    rows = []
    for d in mondays:
        prev, end = d - 1, d + 6
        u = univ(prev, end)
        if len(u) < 20 or prev - 90 not in close["BTCUSDT"]: continue
        rise = {s: close[s][prev] / close[s][prev - 30] - 1 for s in u}
        picks = sorted(u, key=lambda s: -rise[s])[:10]
        rets = []
        for s in picks:
            e0 = close[s][prev]; r = 1 - close[s][end] / e0
            if any(high[s].get(k, 0) >= e0 * 1.40 for k in range(d, end + 1)): r = -0.40
            rets.append(r)
        rows.append(dict(d=d, year=datetime.fromtimestamp(d * DAY / 1000, timezone.utc).year, gate=gate.get(prev, False),
                         wk=S.mean(rets) - 2 * FEE - 0.0003 * 7, med30=S.median(rise.values()),
                         hot=sum(v > 0.5 for v in rise.values()) / len(rise),
                         btc90=close["BTCUSDT"][prev] / close["BTCUSDT"][prev - 90] - 1, tenth=rise[picks[-1]]))
    g = [r for r in rows if r["gate"]]
    print(f"POMPA SHORT, kapi acik haftalar: {len(g)} (2021-2026)\n")
    for key, label, cuts in (("med30", "ilk 50'nin 30g ortanca getirisi", (0.0, 0.10, 0.25)),
                             ("hot", "30 gunde +%50'yi gecenlerin orani", (0.05, 0.10, 0.20)),
                             ("btc90", "BTC 90 gunluk getiri", (0.0, 0.25, 0.50)),
                             ("tenth", "10. secimin 30g yukselisi", (0.5, 1.0, 1.5))):
        print(f"--- {label} ---")
        edges = (-9,) + cuts + (99,)
        for lo, hi in zip(edges, edges[1:]):
            b = [r for r in g if lo <= r[key] < hi]
            if not b: continue
            yrs = sorted({r["year"] for r in b})
            print(f"  {lo:>6} .. {hi:<6}: {len(b):3d} hafta | hafta basi %{100 * S.mean(r['wk'] for r in b):+5.1f} | "
                  f"kazanan %{100 * sum(r['wk'] > 0 for r in b) / len(b):3.0f} | toplam x{math.prod(1 + r['wk'] for r in b):5.2f} | yillar {yrs}")
    print("\n--- 2021 haftalari (kapi acik) ve gostergeleri ---")
    for r in [r for r in g if r["year"] == 2021][:40]:
        print(f"  {datetime.fromtimestamp(r['d'] * DAY / 1000, timezone.utc):%Y-%m-%d} short %{100 * r['wk']:+5.1f} | med30 %{100 * r['med30']:+4.0f} "
              f"hot %{100 * r['hot']:3.0f} btc90 %{100 * r['btc90']:+4.0f} 10.secim +%{100 * r['tenth']:.0f}")
    # a candidate rule per gauge, judged per year
    print("\n--- aday kurallar (kapi acik + kural gecerse short) ---")
    for name, ok in (("kural yok", lambda r: True),
                     ("med30 < %10", lambda r: r["med30"] < 0.10), ("med30 < %25", lambda r: r["med30"] < 0.25),
                     ("hot < %10", lambda r: r["hot"] < 0.10), ("hot < %20", lambda r: r["hot"] < 0.20),
                     ("btc90 < %25", lambda r: r["btc90"] < 0.25), ("btc90 < %50", lambda r: r["btc90"] < 0.50)):
        b = [r for r in g if ok(r)]
        yearly = {}
        for r in b: yearly[r["year"]] = yearly.get(r["year"], 1.0) * (1 + r["wk"])
        eq, peak, mdd = 1.0, 1.0, 0.0
        for r in b:
            eq *= 1 + r["wk"]; peak = max(peak, eq); mdd = max(mdd, 1 - eq / peak)
        print(f"  {name:12}: {len(b):3d} hafta | 100$ -> {100 * eq:7.0f} | en derin dusus %{100 * mdd:3.0f} | "
              + " ".join(f"{y}:x{v:.2f}" for y, v in sorted(yearly.items())))

    # ---------- 2. the pair ----------
    print("\n\nCIFT: yarim BTC long + yarim altcoin sepeti short (haftalik esitleme)")
    for N in (10, 20, 49):
        for mode in ("her zaman", "kapi kapaliyken", "kapi acikken"):
            for funding in (0.0, 0.0003):
                eq, peak, mdd, yearly, wins, n, worst = 1.0, 1.0, 0.0, {}, 0, 0, 0.0
                for d in mondays:
                    prev, end = d - 1, d + 6
                    g0 = gate.get(prev, False)
                    if (mode == "kapi kapaliyken" and g0) or (mode == "kapi acikken" and not g0): continue
                    u = univ(prev, end, 1)[:N]
                    if len(u) < N * 0.8: continue
                    b = close["BTCUSDT"][end] / close["BTCUSDT"][prev] - 1
                    a = S.mean(close[s][end] / close[s][prev] - 1 for s in u)
                    wk = 0.5 * b - 0.5 * a - 2 * FEE * 0.3 - funding * 7
                    eq *= 1 + wk; peak = max(peak, eq); mdd = max(mdd, 1 - eq / peak); n += 1; wins += wk > 0
                    worst = min(worst, wk)
                    y = datetime.fromtimestamp(d * DAY / 1000, timezone.utc).year
                    yearly[y] = yearly.get(y, 1.0) * (1 + wk)
                print(f"  ilk {N:2d} alt, {mode:15}, fonlama %{100 * funding:.2f}/gun: 100$ -> {100 * eq:5.0f} | {n:3d} hafta kazanan %{100 * wins / max(1, n):3.0f} | "
                      f"en kotu hafta %{100 * worst:+.0f} | en derin dusus %{100 * mdd:3.0f} | " + " ".join(f"{y}:x{v:.2f}" for y, v in sorted(yearly.items())))


if __name__ == "__main__":
    main()
