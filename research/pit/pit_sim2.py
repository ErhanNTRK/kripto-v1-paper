"""Three structural fixes on the point-in-time universe (28 Sep 2026):
1. stable-coin filter: new entries only in coins that were in the daily top-50 on >= X of the previous 90 days
2. momentum off, or momentum only in stable coins (breakouts everywhere)
3. wide trailing (4H 6 ATR, 2H 8 ATR)
Same engine/data as pit_sim.py (imported: its module-level code builds BARS/ENT/SELL). Walk-forward as before.
"""
import sys, pickle, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import pit_sim as P

DAY = P.DAY
days = sorted(P.daily)
# stable[day] = coins in the top-50 on >= X of the previous 90 days (yesterday and before; known that morning)
def stable_sets(x_of_90):
    out, window = {}, []
    from collections import Counter
    cnt = Counter()
    for d in days:
        out[d] = {s for s, n in cnt.items() if n >= x_of_90}   # previous <= 90 days only
        window.append(P.daily[d])
        for s in P.daily[d]:
            cnt[s] += 1
        if len(window) > 90:
            for s in window.pop(0):
                cnt[s] -= 1
    return out
STABLE = {60: stable_sets(60), 80: stable_sets(80)}


def run2(label, momentum, trail, stable=None, momentum_stable_only=False, risk=0.01, cap=0.01, quality=(1.5, 0.5)):
    ent = {sn: P.ENT[sn][(True, True)] for sn in ("4H", "2H")}   # ichimoku on; momentum filtered below
    cash = {"4H": 51.0, "2H": 119.0}
    pos = {"4H": {}, "2H": {}}
    pend_buy = {"4H": [], "2H": []}; pend_sell = {"4H": set(), "2H": set()}
    marks, curve, trades = {}, [], []
    st = {"day": None, "day_eq": None, "halted": {"4H": False, "2H": False}}

    def equity(sn):
        return cash[sn] + sum(p["qty"] * marks.get(s, p["entry"]) for s, p in pos[sn].items())

    def close(sn, s, price, t):
        p = pos[sn].pop(s)
        px = price * (1 - P.SLIP)
        cash[sn] += p["qty"] * px * (1 - P.FEE)
        trades.append((sn, s, (px - p["entry"]) / (p["entry"] - p["stop0"]), p["t_in"], t,
                       p["qty"] * (px * (1 - P.FEE) - p["entry"] * (1 + P.FEE)), p["kind"]))

    def step(sn, t):
        bars = P.BARS[sn]
        if t not in bars["BTCUSDT"]:
            return
        for s in list(pend_sell[sn]):
            f = bars.get(s, {}).get(t)
            if s in pos[sn] and f:
                close(sn, s, f[0], t)
        pend_sell[sn] = set()
        eq = equity(sn)
        d = t // DAY * DAY
        allowed = P.daily.get(d, set())
        stab = STABLE[stable].get(d, set()) if stable else None
        stab_m = STABLE[60].get(d, set())
        for s, stop, score, breaks in sorted(pend_buy[sn], key=lambda x: (-x[2], x[0])):
            if st["halted"][sn] or len(pos[sn]) >= P.MAXPOS[sn]:
                break
            f = bars.get(s, {}).get(t)
            if not f or s in pos["4H"] or s in pos["2H"] or s not in allowed:
                continue
            is_mom = breaks < 1
            if stab is not None and s not in stab:
                continue
            if is_mom and (not momentum or (momentum_stable_only and s not in stab_m)):
                continue
            entry = f[0] * (1 + P.SLIP)
            if stop <= 0 or entry <= stop:
                continue
            unit = entry * (1 + P.FEE) - stop * (1 - P.SLIP) * (1 - P.FEE)
            r = risk * (quality[0] if breaks >= 1 else quality[1])
            qty = min(eq * r / unit, P.LEV * eq / (entry * (1 + P.FEE)))
            mn = P.MIN_NOTIONAL.get(s, 5.0) * 1.03
            if qty * entry < mn:
                q2 = mn / entry
                if q2 * unit > eq * cap:
                    continue
                qty = q2
            cash[sn] -= qty * entry * (1 + P.FEE)
            pos[sn][s] = dict(qty=qty, entry=entry, stop=stop, stop0=stop, unit=unit, high=entry, t_in=t,
                              kind="momentum" if is_mom else "kirilim")
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
            if t in P.SELL[sn].get(s, ()):
                pend_sell[sn].add(s)
            p["high"] = max(p["high"], f[1])
            if p["high"] >= p["entry"] + p["unit"] and f[4]:
                p["stop"] = max(p["stop"], p["high"] - trail[sn] * f[4])
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
    T_CUR, T_WIDE = P.TRAIL["simdiki"], P.TRAIL["genis"]
    V = [
        ("0 BUGUNKU KURALLAR", dict(momentum=True, trail=T_CUR)),
        ("1a kalici 60/90", dict(momentum=True, trail=T_CUR, stable=60)),
        ("1b kalici 80/90", dict(momentum=True, trail=T_CUR, stable=80)),
        ("2a momentum kapali", dict(momentum=False, trail=T_CUR)),
        ("2b momentum sadece kalicida", dict(momentum=True, trail=T_CUR, momentum_stable_only=True)),
        ("3 genis iz", dict(momentum=True, trail=T_WIDE)),
        ("1a+2a kalici60 + mom kapali", dict(momentum=False, trail=T_CUR, stable=60)),
        ("1a+3 kalici60 + genis", dict(momentum=True, trail=T_WIDE, stable=60)),
        ("2a+3 mom kapali + genis", dict(momentum=False, trail=T_WIDE)),
        ("2b+3 mom kalicida + genis", dict(momentum=True, trail=T_WIDE, momentum_stable_only=True)),
        ("1a+2a+3 kalici60 + mom kapali + genis", dict(momentum=False, trail=T_WIDE, stable=60)),
        ("1b+2a+3 kalici80 + mom kapali + genis", dict(momentum=False, trail=T_WIDE, stable=80)),
    ]
    res = {}
    for label, kw in V:
        r = run2(label, **kw)
        res[label] = r
        eqs = [e for _, e in r["curve"]]; rs = [x[2] for x in r["trades"]]
        print(f"{label:42} 3 yil {eqs[-1]/170:6.1f}x | islem {len(rs)} | ort {sum(rs)/max(len(rs),1):+.2f}R", flush=True)
    pickle.dump(res, open(Path(__file__).with_suffix(".pkl"), "wb"))
    from datetime import datetime, timezone
    ts = lambda y, m: int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)
    WIN = [(ts(2023, 9), ts(2024, 3)), (ts(2024, 3), ts(2024, 9)), (ts(2024, 9), ts(2025, 3)),
           (ts(2025, 3), ts(2025, 9)), (ts(2025, 9), ts(2026, 3)), (ts(2026, 3), ts(2026, 9))]
    NM = ["Eyl23-Sub24", "Mar24-Agu24", "Eyl24-Sub25", "Mar25-Agu25", "Eyl25-Sub26", "Mar26-Agu26"]

    def eq_at(c, t, first):
        if first:
            return next((e for tt, e in c if tt >= t), c[-1][1])
        return next((e for tt, e in reversed(c) if tt <= t), c[0][1])

    def ratio(c, a, b):
        return eq_at(c, b, False) / eq_at(c, a, True)

    def ddn(c):
        pk, m = None, 0.0
        for _, e in c:
            pk = e if pk is None else max(pk, e)
            m = max(m, 1 - e / pk)
        return m

    def pf(tr):
        w = sum(x[5] for x in tr if x[5] > 0); l = -sum(x[5] for x in tr if x[5] <= 0)
        return w / l if l else 0

    print("\n=== TUM DONEM + 6 AYLIK DONEMLER + SON 12 AY ===")
    print(f"{'':42} {'3 yil':>7} {'PF':>5} {'dusus':>6} " + " ".join(f"{n:>11}" for n in NM) + f" {'son12ay':>8}")
    for k, r in res.items():
        c = r["curve"]
        per = [ratio(c, a, b) for a, b in WIN]
        print(f"{k:42} {c[-1][1]/170:6.1f}x {pf(r['trades']):5.2f} %{100*ddn(c):4.0f} "
              + " ".join(f"{100*(x-1):+10.0f}%" for x in per) + f" {100*(per[4]*per[5]-1):+7.0f}%")
    print("\n=== WALK-FORWARD (son 12 aya gore sec, sonraki 6 ayda uygula) ===")
    wf, wf12 = 1.0, []
    for (a, b), n in zip(WIN[2:], NM[2:]):
        best = max(res, key=lambda k: ratio(res[k]["curve"], a - 365 * DAY, a))
        rb = ratio(res[best]["curve"], a, b)
        wf *= rb; wf12.append(rb)
        print(f"{n}: secilen [{best}] -> {100*(rb-1):+.0f}%")
    cur = res["0 BUGUNKU KURALLAR"]["curve"]
    c2 = 1.0
    for a, b in WIN[2:]:
        c2 *= ratio(cur, a, b)
    print(f"2 yil (Eyl24-Agu26): walk-forward {100*(wf-1):+.0f}% | bugunku kurallar {100*(c2-1):+.0f}% | "
          f"WF son 12 ay {100*(wf12[2]*wf12[3]-1):+.0f}%")
    print("BITTI", flush=True)
