"""The user's "frequent dip" + "only when the market is calm" test (30 Sep 2026), on 5-minute USD-M futures bars,
Sep 2024 - Aug 2026 (two 12-month eras), point-in-time daily top-50.
For each coin in the day's list (top 10 or all 50): a buy triggers on the first 5m bar whose low reaches
(yesterday's close) x (1 - depth); fill at that level (or the bar open if it opened below), costs 0.07%/side.
BTC filter at the trigger bar: none / BTC's lowest price so far today no more than 3% (or 2%) under its own
yesterday close ("the market is calm, only this coin dipped").
Exit from the NEXT bar on: stop first, then take-profit, else the close 24 hours after the fill; a stop inside the
fill bar counts (conservative), a take-profit inside it does not. Returns are per trade on the position
(unlevered); x5 leverage multiplies them on the margin.
Printed: every variant positive in BOTH eras, plus the live dip-catcher's rule for reference."""
import csv, io, json, statistics as S, time, zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

D5 = Path(r"C:\Users\ASUS-PC\kripto\arastirma-verisi\fut5m")
PIT = Path(r"C:\Users\ASUS-PC\kripto\arastirma-verisi\pit")
DAY, M5 = 86_400_000, 300_000
COST = 0.0007
ts = lambda y, m: int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)
ERAS = [("Eyl24-Agu25", ts(2024, 9), ts(2025, 9)), ("Eyl25-Agu26", ts(2025, 9), ts(2026, 9))]
DEPTHS = (0.04, 0.06, 0.08, 0.10, 0.15)
TPS = (0.02, 0.03, 0.05, 0.08)
STOPS = (0.03, 0.05, 0.10, 0.25)
BTC_CAPS = (None, 0.03, 0.02)
daily = {int(k): v for k, v in json.loads((PIT / "daily_top50.json").read_text()).items()}
months = [f"{y}-{m:02d}" for y in (2024, 2025, 2026) for m in range(1, 13) if "2024-09" <= f"{y}-{m:02d}" <= "2026-08"]


def load(sym):
    out = {}
    for m in months:
        p = D5 / f"{sym}-5m-{m}.zip"
        if not p.exists():
            continue
        with zipfile.ZipFile(p) as z:
            for r in csv.reader(io.StringIO(z.read(z.namelist()[0]).decode())):
                if r and r[0].isdigit():
                    out[int(r[0])] = (float(r[1]), float(r[2]), float(r[3]), float(r[4]))
    return out


t0 = time.time()
btc = load("BTCUSDT")
btc_prev = {}      # day -> BTC close of the last 5m bar before it
btc_low_sofar = {} # 5m bar time -> BTC's lowest low of the day up to and including that bar
for d in sorted({t // DAY * DAY for t in btc}):
    prev = btc.get(d - M5)
    if not prev:
        continue
    btc_prev[d] = prev[3]
    low = float("inf")
    for k in range(288):
        b = btc.get(d + k * M5)
        if b:
            low = min(low, b[2]); btc_low_sofar[d + k * M5] = low
print(f"BTC hazir ({round(time.time()-t0)} sn)", flush=True)

res = defaultdict(list)   # (era, universe, depth, cap, tp, stop) -> [return]
symbols = sorted({s for d, v in daily.items() if d >= ERAS[0][1] for s in v if s != "BTCUSDT"})
for n, sym in enumerate(symbols):
    bars = load(sym)
    if not bars:
        continue
    for d in sorted({t // DAY * DAY for t in bars}):
        era = next((e for e, a, b in ERAS if a <= d < b), None)
        lst = [s for s in daily.get(d, []) if s != "BTCUSDT"]
        if era is None or sym not in lst or d not in btc_prev:
            continue
        rank = lst.index(sym)
        prev = bars.get(d - M5)
        if not prev:
            continue
        for depth in DEPTHS:
            level = prev[3] * (1 - depth)
            fill_t = next((d + k * M5 for k in range(288) if (d + k * M5) in bars and bars[d + k * M5][2] <= level), None)
            if fill_t is None:
                continue
            fb = bars[fill_t]
            entry = min(level, fb[0]) * (1 + COST)
            btc_drop = 1 - btc_low_sofar.get(fill_t, btc_prev[d]) / btc_prev[d]
            later = [bars[fill_t + j * M5] for j in range(1, 289) if (fill_t + j * M5) in bars]
            for tp in TPS:
                for stop in STOPS:
                    sp, tpp = entry * (1 - stop), entry * (1 + tp)
                    if fb[2] <= sp:
                        out = sp
                    else:
                        out = None
                        for b in later:
                            if b[2] <= sp:
                                out = min(b[0], sp); break
                            if b[1] >= tpp:
                                out = max(b[0], tpp); break
                        if out is None:
                            out = (later[-1] if later else fb)[3]
                    r = out * (1 - COST) / entry - 1
                    for cap in BTC_CAPS:
                        if cap is not None and btc_drop > cap:
                            continue
                        res[(era, "ilk50", depth, cap, tp, stop)].append((r, d))
                        if rank < 10:
                            res[(era, "ilk10", depth, cap, tp, stop)].append((r, d))
    if n % 20 == 0:
        print(f"  {n}/{len(symbols)} coin ({round(time.time()-t0)} sn)", flush=True)
    del bars


def st(rs):
    if not rs:
        return None
    x = [r for r, _ in rs]
    w = sum(v for v in x if v > 0); l = -sum(v for v in x if v <= 0)
    days = len({d for _, d in rs})
    return dict(n=len(x), days=days, win=sum(v > 0 for v in x) / len(x), avg=S.mean(x), pf=w / l if l else 9.99, tot=sum(x))


def fmt(s):
    if not s:
        return "yok".rjust(46)
    return (f"n {s['n']:4d} ({s['n']/52:4.1f}/hf) kaz %{100*s['win']:3.0f} ort {100*s['avg']:+5.2f}% PF {s['pf']:4.2f}")


keys = sorted({k[1:] for k in res}, key=lambda k: tuple(-1 if v is None else v for v in k))
print("\n=== IKI DONEMDE DE ARTIDA OLAN VARYANTLAR (islem basina, kaldiracsiz; x5 kaldiracla kasaya etkisi 5 kati) ===")
print(f"{'liste':6} {'dip':>5} {'BTC':>9} {'kar':>5} {'stop':>5} | {ERAS[0][0]:^46} | {ERAS[1][0]:^46}")
good = []
for k in keys:
    a, b = st(res.get((ERAS[0][0],) + k)), st(res.get((ERAS[1][0],) + k))
    if a and b and a["avg"] > 0 and b["avg"] > 0 and a["n"] >= 20 and b["n"] >= 20:
        good.append((min(a["pf"], b["pf"]), k, a, b))
for _, k, a, b in sorted(good, reverse=True)[:40]:
    uni, depth, cap, tp, stop = k
    capt = "hepsi" if cap is None else f"<=%{int(cap*100)}"
    print(f"{uni:6} -%{int(depth*100):3d} {capt:>9} +%{int(tp*100):3d} -%{int(stop*100):3d} | {fmt(a)} | {fmt(b)}")
print(f"\nuygun varyant sayisi: {len(good)} / {len(keys)}")
print("\n=== referans: canli igne kurali (ilk 10, -%15, +%8, -%25), BTC filtresiz ve BTC<=%3 ===")
for cap in (None, 0.03):
    k = ("ilk10", 0.15, cap, 0.08, 0.25)
    print(f"  BTC {'hepsi' if cap is None else '<=%3'}: {fmt(st(res.get((ERAS[0][0],) + k)))} | {fmt(st(res.get((ERAS[1][0],) + k)))}")
print(f"BITTI {round((time.time()-t0)/60)} dk", flush=True)
