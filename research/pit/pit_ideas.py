"""Ideas from the GitHub survey, on the point-in-time universe with the LIVE rules (breakout only, trail 4H 6 / 2H 8):
A) global StoplossGuard (freqtrade): N initial-stop exits within L days -> no new entries for D days (both systems)
B) volatility targeting (Zarattini 2025): risk x min(cap, target / BTC realised vol over `lb` days)
C) Turtle-style pyramiding is skipped (min-notional on a ~226 USDT account).
Same engine as pit_sim2.run2 (copied, with the two hooks). 6-month periods + last 12 months + worst period."""
import sys, math, pickle
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import pit_sim2 as Q
P = Q.P
DAY = P.DAY
TRAIL = {"4H": 6.0, "2H": 8.0}

# BTC daily closes from 4H bars -> realised vol (annualised) known at the start of each day
btc4 = P.BARS["4H"]["BTCUSDT"]
dclose = {}
for t in sorted(btc4):
    if (t + P.H4) % DAY == 0:   # FIXED 28 Sep: only the last 4H bar of a day (was intraday lookahead)
        dclose[(t + P.H4) // DAY * DAY] = btc4[t][3]
dkeys = sorted(dclose)
def vol_table(lb):
    out = {}
    for i, d in enumerate(dkeys):
        if i < lb + 1:
            continue
        rets = [math.log(dclose[dkeys[j]] / dclose[dkeys[j - 1]]) for j in range(i - lb + 1, i + 1)]
        m = sum(rets) / lb
        out[d] = math.sqrt(sum((r - m) ** 2 for r in rets) / (lb - 1)) * math.sqrt(365)
    return out
VOL = {lb: vol_table(lb) for lb in (30, 90)}


def run3(guard=None, vt=None, risk=0.01, cap=0.01, quality=(1.5, 0.5)):
    ent = {sn: P.ENT[sn][(True, False)] for sn in ("4H", "2H")}      # ichimoku on, momentum OFF
    cash = {"4H": 51.0, "2H": 119.0}
    pos = {"4H": {}, "2H": {}}
    pend_buy = {"4H": [], "2H": []}; pend_sell = {"4H": set(), "2H": set()}
    marks, curve, trades = {}, [], []
    st = {"day": None, "day_eq": None, "halted": {"4H": False, "2H": False}, "guard_until": -1, "stops": []}

    def equity(sn):
        return cash[sn] + sum(p["qty"] * marks.get(s, p["entry"]) for s, p in pos[sn].items())

    def close(sn, s, price, t, why):
        p = pos[sn].pop(s)
        px = price * (1 - P.SLIP)
        cash[sn] += p["qty"] * px * (1 - P.FEE)
        trades.append((sn, s, (px - p["entry"]) / (p["entry"] - p["stop0"]), p["t_in"], t,
                       p["qty"] * (px * (1 - P.FEE) - p["entry"] * (1 + P.FEE)), why))
        if guard and why == "stop" and p["stop"] == p["stop0"]:
            n, L, D = guard
            st["stops"] = [x for x in st["stops"] if x > t - L * DAY] + [t]
            if len(st["stops"]) >= n:
                st["guard_until"] = max(st["guard_until"], t + D * DAY)

    def step(sn, t):
        bars = P.BARS[sn]
        if t not in bars["BTCUSDT"]:
            return
        for s in list(pend_sell[sn]):
            f = bars.get(s, {}).get(t)
            if s in pos[sn] and f:
                close(sn, s, f[0], t, "signal")
        pend_sell[sn] = set()
        eq = equity(sn)
        d = t // DAY * DAY
        allowed = P.daily.get(d, set())
        mult = 1.0
        if vt:
            target, lb, mcap = vt
            v = VOL[lb].get(d)
            mult = min(mcap, target / v) if v else 1.0
        for s, stop, score, breaks in sorted(pend_buy[sn], key=lambda x: (-x[2], x[0])):
            if st["halted"][sn] or len(pos[sn]) >= P.MAXPOS[sn] or t < st["guard_until"]:
                break
            f = bars.get(s, {}).get(t)
            if not f or s in pos["4H"] or s in pos["2H"] or s not in allowed or breaks < 1:
                continue
            entry = f[0] * (1 + P.SLIP)
            if stop <= 0 or entry <= stop:
                continue
            unit = entry * (1 + P.FEE) - stop * (1 - P.SLIP) * (1 - P.FEE)
            r = risk * quality[0] * mult
            qty = min(eq * r / unit, P.LEV * eq / (entry * (1 + P.FEE)))
            mn = P.MIN_NOTIONAL.get(s, 5.0) * 1.03
            if qty * entry < mn:
                q2 = mn / entry
                if q2 * unit > eq * cap:
                    continue
                qty = q2
            cash[sn] -= qty * entry * (1 + P.FEE)
            pos[sn][s] = dict(qty=qty, entry=entry, stop=stop, stop0=stop, unit=unit, high=entry, t_in=t)
        pend_buy[sn] = []
        for s, p in list(pos[sn].items()):
            f = bars.get(s, {}).get(t)
            if f and f[2] <= p["stop"]:
                close(sn, s, min(f[0], p["stop"]), t, "stop")
        for s, p in pos[sn].items():
            f = bars.get(s, {}).get(t)
            if not f:
                continue
            marks[s] = f[3]
            if t in P.SELL[sn].get(s, ()):
                pend_sell[sn].add(s)
            p["high"] = max(p["high"], f[1])
            if p["high"] >= p["entry"] + p["unit"] and f[4]:
                p["stop"] = max(p["stop"], p["high"] - TRAIL[sn] * f[4])
        if not st["halted"][sn]:
            pend_buy[sn] = [x for x in ent[sn].get(t, []) if x[0] not in pos[sn]]

    for t in P.times2:
        dd_ = t // DAY
        if dd_ != st["day"]:
            st["day"], st["day_eq"] = dd_, equity("4H") + equity("2H")
            st["halted"] = {"4H": False, "2H": False}
        if (t + P.H2) % P.H4 == 0 and (t - P.H2) in P.BARS["4H"]["BTCUSDT"]:
            step("4H", t - P.H2)
        step("2H", t)
        eq = equity("4H") + equity("2H")
        if eq <= st["day_eq"] * 0.9 and not all(st["halted"].values()):
            for sn in ("4H", "2H"):
                st["halted"][sn] = True
                pend_sell[sn] |= set(pos[sn])
        curve.append((t + P.H2, eq))
    return {"curve": curve, "trades": trades}


if __name__ == "__main__":
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
        big = sorted((x[2] for x in r["trades"]), reverse=True)
        print(f"{label:36} {c[-1][1]/170:6.1f}x PF {pf(r['trades']):4.2f} dusus %{100*ddn(c):3.0f} | "
              + " ".join(f"{100*(x-1):+5.0f}%" for x in per)
              + f" | son12 {100*(per[4]*per[5]-1):+4.0f}% | en kotu donem {100*(min(per)-1):+4.0f}% | islem {len(big)} 5R+ {sum(x>=5 for x in big)}", flush=True)
    res = {}
    def go(label, **kw):
        res[label] = run3(**kw); show(label, res[label])
    go("CANLI (referans)")
    print("--- A) toplu stop freni: N stop / L gun -> D gun alim yok")
    for n in (3, 4, 5, 6):
        for L in (2, 3):
            for D in (2, 4, 7):
                go(f"fren {n} stop/{L}g -> {D}g", guard=(n, L, D))
    print("--- B) volatilite hedefi: risk x min(tavan, hedef / BTC yillik vol)")
    for lb in (30, 90):
        for target in (0.35, 0.45, 0.55, 0.70):
            for mcap in (1.0, 1.5):
                go(f"vol hedef %{int(target*100)} {lb}g tavan {mcap}", vt=(target, lb, mcap))
    pickle.dump(res, open(Path(__file__).with_suffix(".pkl"), "wb"))
    print("BITTI", flush=True)
