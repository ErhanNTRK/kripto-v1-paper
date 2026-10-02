"""Should the bot scan more coins? (user, 2 Oct 2026: "widen the list, opportunities are being missed").
The live 2H system (3-ATR stop, 8-ATR trail, 10 positions, 1% risk with the 1.5/0.5 quality sizing, 3% minimum
stop, 24h pump filter, break-even at +2%, ETH/BTC gate, daily -10% halt) on 2H bars built from the stored 5m
futures data of 305 coins, Sep 2025 - Aug 2026. Entries only in that day's top-N by the previous 7 days' quote
volume (N = 50 / 100 / 200 / all). Costs: 0.05% fee + 0.02% slippage per side, 4x margin cap.
Result 2 Oct 2026: the 5m data starts Jul 2025, so BTC's 200-day regime average only exists from ~Feb 2026 and
the first six months take no trades; only Mar-Aug 2026 counts. There, 100$ became 134 (top 50), 119 (top 100),
128 (top 200), 113 (all 305) with 1.1 / 1.8 / 2.7 / 3.2 entries a week and deeper drawdowns (25% -> 37%)."""
import csv, glob, io, json, os, sys, time, zipfile
from collections import defaultdict, deque
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

REPO = "C:/Users/ASUS-PC/Documents/GitHub/kripto-v1-paper"; sys.path.insert(0, REPO)
DATA = Path(r"C:\Users\ASUS-PC\kripto\arastirma-verisi\fut5m")
MONTHS = [f"2025-{m:02d}" for m in range(7, 13)] + [f"2026-{m:02d}" for m in range(1, 9)]
DAY, H2 = 86_400_000, 7_200_000
START = 1_756_684_800_000
FEE, SLIP, LEV = 0.0005, 0.0002, 4
MIN_NOTIONAL = {"ETHUSDT": 20.0, "BCHUSDT": 20.0, "LTCUSDT": 20.0, "BTCUSDT": 100.0}


def load(coin):
    """2H bars and daily quote volume from the monthly 5m zips."""
    bars, qv = {}, defaultdict(float)
    for m in MONTHS:
        for f in glob.glob(str(DATA / f"{coin}-5m-{m}.zip")):
            z = zipfile.ZipFile(f)
            for r in csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]))):
                if not r[0].isdigit():
                    continue
                t = int(r[0]); b = t // H2 * H2
                o, h, l, c, v, q = float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]), float(r[7])
                x = bars.get(b)
                if x is None:
                    bars[b] = {"t": b, "o": o, "h": h, "l": l, "c": c, "v": v}
                else:
                    x["h"] = max(x["h"], h); x["l"] = min(x["l"], l); x["c"] = c; x["v"] += v
                qv[t // DAY * DAY] += q
    rows = [bars[k] for k in sorted(bars)]
    return coin, rows, dict(qv)


def cut(rows):
    keep = rows[:1]
    for a, b in zip(rows, rows[1:]):
        if a["c"] > 0 and (b["o"] / a["c"] > 3 or b["o"] / a["c"] < 1 / 3 or b["t"] - a["t"] > 3 * DAY):
            break
        keep.append(b)
    return keep


def run(D, top_n, s0, s1, trail=8.0, maxpos=10, risk=0.01, quality=1.5, min_stop=0.03, pump=0.20, be=0.02):
    BARS, ENT, SELL, GATE, RANK = D["bars"], D["ent"], D["sell"], D["gate"], D["rank"]
    cash, pos, pend_buy, pend_sell, marks, curve = 170.0, {}, [], set(), {}, []
    cnt = {"giris": 0, "kazanan": 0, "kapanan": 0}
    day, day_eq, halted, peak, mdd = None, None, False, 170.0, 0.0
    def notional(): return sum(p["qty"] * marks.get(s, p["entry"]) for s, p in pos.items())
    def equity(): return cash + notional()
    def close(s, price):
        nonlocal cash
        p = pos.pop(s); back = p["qty"] * price * (1 - SLIP) * (1 - FEE); cash += back
        cnt["kapanan"] += 1; cnt["kazanan"] += back > p["cost"]
    for t in D["times"]:
        if not (s0 <= t < s1 + 30 * DAY):
            continue
        d = t // DAY * DAY
        if t // DAY != day:
            day, day_eq, halted = t // DAY, equity(), False
        for s in list(pend_sell):
            f = BARS.get(s, {}).get(t)
            if s in pos and f: close(s, f[0])
        pend_sell = set()
        eq = equity()
        allowed = RANK.get(d, ())[:top_n] if (s0 <= d < s1 and GATE.get(d, False)) else ()
        allowed = set(allowed)
        for s, stop, score, breaks, pmp in sorted(pend_buy, key=lambda x: (-x[2], x[0])):
            if halted or len(pos) >= maxpos: break
            f = BARS.get(s, {}).get(t)
            if not f or s in pos or s not in allowed or pmp > pump: continue
            entry = f[0] * (1 + SLIP)
            stop = min(stop, entry * (1 - min_stop))
            if stop <= 0 or entry <= stop: continue
            unit = entry * (1 + FEE) - stop * (1 - SLIP) * (1 - FEE)
            r = risk * (quality if breaks >= 1 else 0.5)
            qty = min(eq * r / unit, LEV * eq / (entry * (1 + FEE)))
            mn = MIN_NOTIONAL.get(s, 5.0) * 1.03
            if qty * entry < mn:
                if (mn / entry) * unit > eq * r: continue
                qty = mn / entry
            if notional() + qty * entry > LEV * eq: continue
            cash -= qty * entry * (1 + FEE); cnt["giris"] += 1
            pos[s] = dict(qty=qty, entry=entry, stop=stop, unit=unit, high=entry, cost=qty * entry * (1 + FEE))
        pend_buy = []
        for s, p in list(pos.items()):
            f = BARS.get(s, {}).get(t)
            if f and f[2] <= p["stop"]: close(s, min(f[0], p["stop"]))
        for s, p in pos.items():
            f = BARS.get(s, {}).get(t)
            if not f: continue
            marks[s] = f[3]
            if t in SELL.get(s, ()): pend_sell.add(s)
            p["high"] = max(p["high"], f[1])
            if p["high"] >= p["entry"] + p["unit"] and f[4]:
                p["stop"] = max(p["stop"], p["high"] - trail * f[4])
            if be and p["high"] >= p["entry"] * (1 + be):
                p["stop"] = max(p["stop"], p["entry"] * (1 + 2 * FEE + 2 * SLIP))
        if not halted:
            pend_buy = [x for x in ENT.get(t, []) if x[0] not in pos]
        e = equity()
        if e <= day_eq * 0.9 and not halted:
            halted = True; pend_sell |= set(pos)
        if t < s1:
            peak = max(peak, e); mdd = max(mdd, 1 - e / peak); curve.append(e)
    return curve, cnt, mdd


if __name__ == "__main__":
    import crypto_v1.research_v5 as rv5
    from crypto_v1.research_v5 import ShortWindowLongModel as M
    from crypto_v1.github_worker import load_2h_strategy
    os.chdir(REPO)
    t0 = time.time()
    coins = sorted({Path(f).name.split("-5m-")[0] for f in glob.glob(str(DATA / "*-5m-*.zip"))})
    with ProcessPoolExecutor(10) as pool:
        loaded = {c: (rows, qv) for c, rows, qv in pool.map(load, coins, chunksize=4)}
    print(f"{len(loaded)} coin yuklendi ({round(time.time()-t0)} sn)", flush=True)
    cfg = dict(load_2h_strategy(relax=False), entry_mode="breakout")
    btc_rows = rv5.symmetric_features(loaded["BTCUSDT"][0], cfg)
    btc = {r["t"]: r for r in btc_rows}
    bars, ent, sell = {}, defaultdict(list), {}
    for s, (rows, qv) in loaded.items():
        if s == "BTCUSDT":
            continue
        rows = cut(rows)
        if len(rows) < 300:
            continue
        b, sl, lows = {}, set(), deque(maxlen=12)
        for f in rv5.symmetric_features(rows, cfg):
            t = f["t"]; lows.append(f["l"])
            b[t] = (f["o"], f["h"], f["l"], f["c"], f.get("atr"))
            bt = btc.get(t)
            if bt is None: continue
            if M.sell(f, bt, cfg): sl.add(t)
            if M.buy(f, bt, cfg):
                vr = f["v"] / f["volume_avg"] if f.get("volume_avg") else 0
                ent[t].append((s, f["c"] - 3.0 * f["atr"], f.get("signal_score", vr), f.get("breaks_up", 0),
                               f["c"] / min(lows) - 1 if min(lows) > 0 else 0))
        bars[s], sell[s] = b, sl
    # ETH/BTC > its 50-day EMA, from daily closes known at the start of the day
    def dcl(sym):
        return {(t + H2) // DAY * DAY: v[3] for t, v in sorted(bars[sym].items()) if (t + H2) % DAY == 0}
    bars["BTCUSDT"] = {r["t"]: (r["o"], r["h"], r["l"], r["c"], r.get("atr")) for r in btc_rows}
    e, bb = dcl("ETHUSDT"), dcl("BTCUSDT")
    gate, m = {}, None
    for d in sorted(set(e) & set(bb)):
        r = e[d] / bb[d]; m = r if m is None else m + (r - m) * 2 / 51; gate[d] = r > m
    # each day's ranking by the previous 7 days' quote volume
    days = sorted({d for _, (_, qv) in loaded.items() for d in qv})
    rank = {}
    for d in days:
        vol = []
        for s, (_, qv) in loaded.items():
            if s == "BTCUSDT" or s not in bars: continue
            v7 = sum(qv.get(d - k * DAY, 0) for k in range(1, 8))
            if v7 > 0: vol.append((v7, s))
        rank[d] = [s for _, s in sorted(vol, reverse=True)]
    times = sorted(bars["BTCUSDT"])
    D = dict(bars=bars, ent=ent, sell=sell, gate=gate, rank=rank, times=times)
    print(f"hazir ({round(time.time()-t0)} sn) | ETH/BTC kapisi acik gun %{100*sum(gate.get(d,0) for d in days if d>=START)/max(1,sum(1 for d in days if d>=START)):.0f}\n", flush=True)
    HALF = START + 182 * DAY; END = START + 365 * DAY
    for n in (50, 100, 200, 400):
        line = f"ilk {n if n < 400 else 'hepsi (305)':>11} |"
        for name, a, b in (("12 ay", START, END), ("ilk 6 ay", START, HALF), ("son 6 ay", HALF, END)):
            curve, cnt, mdd = run(D, n, a, b)
            weeks = (b - a) / (7 * DAY)
            line += (f" {name}: 100$->{100*curve[-1]/170:4.0f}$ (en derin dusus %{100*mdd:.0f}) "
                     f"haftada {cnt['giris']/weeks:4.1f} alim, karli %{100*cnt['kazanan']/max(1,cnt['kapanan']):.0f} |")
        print(line, flush=True)
    print(f"BITTI ({round(time.time()-t0)} sn)")
