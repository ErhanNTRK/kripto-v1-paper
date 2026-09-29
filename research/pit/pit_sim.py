"""Point-in-time + walk-forward test of Bulutların Efendisi (28 Sep 2026).

Universe: every day, the top 50 by 7-day average Spot quote volume among USDT pairs that also have a
USDT-M perpetual listed >= 360 days (pit_download.py -> daily_top50.json). New entries only in that
day's list; open positions keep being managed after a coin drops out (as live).
Engine: same as exit_rules.py (live setup: risk 1%, quality 1.5/0.5, round-up to minimum up to 1%,
30/70 split, 6/10 positions, daily 10% halt, next-bar-open entries, intrabar stops, close-based trail).
Grid: ichimoku on/off x momentum on/off x trailing (narrow / current / wide) = 12 combos, each run once.
Walk-forward: every 6 months pick the combo with the best trailing-12-month return (past only) and
stitch its next-6-month return.
"""
import json, math, sys, datetime, pickle, time
from collections import defaultdict
from pathlib import Path
REPO = "C:/Users/ASUS-PC/Documents/GitHub/kripto-v1-paper"; sys.path.insert(0, REPO)
import crypto_v1.research_v5 as rv5
from crypto_v1.research_v5 import ShortWindowLongModel as M
from crypto_v1.risk import validate_config

import os
PIT = Path(os.environ.get("PIT_DIR", r"C:\Users\ASUS-PC\kripto\arastirma-verisi\pit"))
SRC = Path(r"C:\Users\ASUS-PC\kripto\src")
DAY = 86_400_000; H4, H2 = 4 * 3600 * 1000, 2 * 3600 * 1000
BASE = {"4H": validate_config(json.loads((SRC / "config_v5_long.json").read_text(encoding="utf-8"))),
        "2H": validate_config(json.loads((SRC / "config_v5_long_2h.json").read_text(encoding="utf-8")))}
FEE, SLIP, LEV = BASE["4H"]["fee"], BASE["4H"]["slippage"], 4
MIN_NOTIONAL = {"ETHUSDT": 20.0, "BCHUSDT": 20.0, "LTCUSDT": 20.0, "BTCUSDT": 100.0}
MAXPOS = {"4H": 6, "2H": 10}
TRAIL = {"dar": {"4H": 3.0, "2H": 4.0}, "simdiki": {"4H": 4.0, "2H": 6.0}, "genis": {"4H": 6.0, "2H": 8.0}}

daily = {int(k): set(v) for k, v in json.loads((PIT / "daily_top50.json").read_text()).items()}
ever = sorted(set().union(*daily.values()))
START = min(daily); END = max(daily) + DAY
t0 = time.time()

# ---- compact precompute: per system, per symbol -> bars (o,h,l,c,atr), entry lists per combo, sell times ----
COMBO_KEYS = [(i, m) for i in (True, False) for m in (True, False)]
BARS, ENT, SELL = {}, {}, {}
for sysname, fl in (("4H", "4h"), ("2H", "2h")):
    raw = json.loads((PIT / f"data_{fl}.json").read_text(encoding="utf-8"))
    c = dict(BASE[sysname], ichimoku_filter=True, entry_mode="breakout+momentum")  # live config is breakout-only since 111c1de; features must include momentum
    cfgs = {(i, m): dict(BASE[sysname], ichimoku_filter=i, entry_mode="breakout+momentum" if m else "breakout")
            for i, m in COMBO_KEYS}
    btc_rows = rv5.symmetric_features(raw.pop("BTCUSDT"), c)
    btc = {r["t"]: r for r in btc_rows}
    bars = {"BTCUSDT": {r["t"]: (r["o"], r["h"], r["l"], r["c"], r.get("atr")) for r in btc_rows if r["t"] >= START - 30 * DAY}}
    ent = {k: defaultdict(list) for k in COMBO_KEYS}
    sell = {}
    for s in list(raw):
        rows = raw.pop(s)
        if s not in ever or len(rows) < 250:
            continue
        b, sl = {}, set()
        for f in rv5.symmetric_features(rows, c):
            t = f["t"]
            if t < START - 30 * DAY:
                continue
            b[t] = (f["o"], f["h"], f["l"], f["c"], f.get("atr"))
            bt = btc.get(t)
            if bt is None:
                continue
            if M.sell(f, bt, BASE[sysname]):
                sl.add(t)
            for k, cc in cfgs.items():
                if M.buy(f, bt, cc):
                    vr = f["v"] / f["volume_avg"] if f.get("volume_avg") else 0
                    ent[k][t].append((s, M.stop(f, cc), f.get("signal_score", vr), f.get("breaks_up", 0)))
        bars[s], sell[s] = b, sl
    BARS[sysname], ENT[sysname], SELL[sysname] = bars, ent, sell
    del raw, btc, btc_rows
    print(f"{sysname} hazir: {len(bars)} sembol ({round(time.time()-t0)} sn)", flush=True)
times2 = sorted(t for t in BARS["2H"]["BTCUSDT"] if START <= t < END)


def run(label, ichimoku, momentum, trail, risk=0.01, cap=0.01, quality=(1.5, 0.5)):
    ent = {sn: ENT[sn][(ichimoku, momentum)] for sn in ("4H", "2H")}
    cash = {"4H": 51.0, "2H": 119.0}
    pos = {"4H": {}, "2H": {}}
    pend_buy = {"4H": [], "2H": []}; pend_sell = {"4H": set(), "2H": set()}
    marks, curve, trades = {}, [], []
    st = {"day": None, "day_eq": None, "halted": {"4H": False, "2H": False}}

    def equity(sn):
        return cash[sn] + sum(p["qty"] * marks.get(s, p["entry"]) for s, p in pos[sn].items())

    def close(sn, s, price, t):
        p = pos[sn].pop(s)
        px = price * (1 - SLIP)
        cash[sn] += p["qty"] * px * (1 - FEE)
        trades.append((sn, s, (px - p["entry"]) / (p["entry"] - p["stop0"]), p["t_in"], t,
                       p["qty"] * (px * (1 - FEE) - p["entry"] * (1 + FEE))))

    def step(sn, t):
        bars = BARS[sn]
        if t not in bars["BTCUSDT"]:
            return
        for s in list(pend_sell[sn]):
            f = bars.get(s, {}).get(t)
            if s in pos[sn] and f:
                close(sn, s, f[0], t)
        pend_sell[sn] = set()
        eq = equity(sn)
        allowed = daily.get(t // DAY * DAY, set())
        for s, stop, score, breaks in sorted(pend_buy[sn], key=lambda x: (-x[2], x[0])):
            if st["halted"][sn] or len(pos[sn]) >= MAXPOS[sn]:
                break
            f = bars.get(s, {}).get(t)
            if not f or s in pos["4H"] or s in pos["2H"] or s not in allowed:
                continue
            entry = f[0] * (1 + SLIP)
            if stop <= 0 or entry <= stop:
                continue
            unit = entry * (1 + FEE) - stop * (1 - SLIP) * (1 - FEE)
            r = risk * (quality[0] if breaks >= 1 else quality[1])
            qty = min(eq * r / unit, LEV * eq / (entry * (1 + FEE)))
            mn = MIN_NOTIONAL.get(s, 5.0) * 1.03
            if qty * entry < mn:
                q2 = mn / entry
                if q2 * unit > eq * cap:
                    continue
                qty = q2
            cash[sn] -= qty * entry * (1 + FEE)
            pos[sn][s] = dict(qty=qty, entry=entry, stop=stop, stop0=stop, unit=unit, high=entry, t_in=t)
        pend_buy[sn] = []
        for s, p in list(pos[sn].items()):
            f = bars.get(s, {}).get(t)
            if f and f[2] <= p["stop"]:
                close(sn, s, min(f[0], p["stop"]), t)
        for s, p in pos[sn].items():
            f = bars.get(s, {}).get(t)
            if not f:
                continue
            marks[s] = f[3]
            if t in SELL[sn].get(s, ()):
                pend_sell[sn].add(s)
            p["high"] = max(p["high"], f[1])
            if p["high"] >= p["entry"] + p["unit"] and f[4]:
                p["stop"] = max(p["stop"], p["high"] - trail[sn] * f[4])
        if not st["halted"][sn]:
            pend_buy[sn] = [x for x in ent[sn].get(t, []) if x[0] not in pos[sn]]

    for t in times2:
        d = t // DAY
        if d != st["day"]:
            st["day"], st["day_eq"] = d, equity("4H") + equity("2H")
            st["halted"] = {"4H": False, "2H": False}
        if (t + H2) % H4 == 0 and (t - H2) in BARS["4H"]["BTCUSDT"]:
            step("4H", t - H2)
        step("2H", t)
        eq = equity("4H") + equity("2H")
        if eq <= st["day_eq"] * 0.9 and not all(st["halted"].values()):
            for sn in ("4H", "2H"):
                st["halted"][sn] = True
                pend_sell[sn] |= set(pos[sn])
        curve.append((t + H2, eq))
    return {"label": label, "curve": curve, "trades": trades}


def window_ratio(curve, a, b):
    ea = next((e for t, e in curve if t >= a), None)
    eb = next((e for t, e in reversed(curve) if t <= b), None)
    return eb / ea if ea and eb else 1.0


def dd(xs):
    pk, m = xs[0], 0.0
    for x in xs:
        pk = max(pk, x); m = max(m, 1 - x / pk)
    return m


if __name__ == "__main__":
    combos = [(f"ichimoku {'acik' if i else 'kapali'}, momentum {'acik' if m else 'kapali'}, iz {tr}", i, m, tr)
              for i in (True, False) for m in (True, False) for tr in ("simdiki", "dar", "genis")]
    results = {}
    # Control: the same engine and data, but today's fixed top-50 list (the old test's universe).
    old_syms = set(json.loads(Path(r"C:\Users\ASUS-PC\kripto\arastirma-verisi\meta.json").read_text())["symbols"])
    static = old_syms & set(BARS["2H"]) & set(BARS["4H"])
    pit_daily = daily
    daily = {d: static for d in pit_daily}
    ctl = run("KONTROL: bugunku sabit liste, simdiki kurallar", True, True, TRAIL["simdiki"])
    daily = pit_daily
    eqs = [e for _, e in ctl["curve"]]; rs = [x[2] for x in ctl["trades"]]
    print(f"{ctl['label']:48} 3 yil {eqs[-1]/170:7.1f}x (dusus %{100*dd(eqs):.0f}) | islem {len(rs)} | ort {sum(rs)/len(rs):+.2f}R "
          f"| sabit listede {len(static)}/{len(old_syms)} coin", flush=True)
    results[ctl["label"]] = ctl
    for label, i, m, tr in combos:
        res = run(label, i, m, TRAIL[tr])
        eqs = [e for _, e in res["curve"]]
        rs = [x[2] for x in res["trades"]]
        results[label] = res
        print(f"{label:48} 3 yil {eqs[-1]/170:7.1f}x (dusus %{100*dd(eqs):.0f}) | islem {len(rs)} | ort {sum(rs)/max(len(rs),1):+.2f}R "
              f"| {round(time.time()-t0)} sn", flush=True)
    pickle.dump({k: {"curve": v["curve"], "trades": v["trades"]} for k, v in results.items()},
                open(Path(__file__).with_suffix(".pkl"), "wb"))
    print("BITTI", flush=True)
