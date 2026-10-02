"""Stop-and-reverse test (user, 2 Oct 2026: "5x long and 2x short together; when the long is stopped the short keeps
earning with a stop pulled down as price falls; re-long on the next buy signal").
The 2H system with the live rules (3-ATR stop, 8-ATR trail, 10 positions, 3% min stop, pump filter, break-even at
+2%), 12-month windows starting every month in both eras (point-in-time top-50, realistic costs). Variants:
  long only      today's bot
  user's hedge   long 5 + short 2 opened together = a net long of 3/5 the size; when the long is STOPPED (a loss),
                 the short (2/5 of the original long) stays open
  reverse        the full long; when it is stopped, a short of 2/5 its size is opened at the stop
Short management: stop first at the long's entry price, then trailed TRAIL_S ATR above the lowest low; closed
when that stop is hit, when the coin gives a new buy signal (the long re-enters), or after 60 bars.
Also: after every long stop-out, how often did the price fall a further 5% before getting back above the entry?
Result 2 Oct 2026 (typical 12 months per 100$, bad era / good era): long only 75 / 215; user's hedge 86 / 145;
reverse 75 / 204. After a stop the price fell 5% further first in 30-33% of cases and came back to the entry
first in 48-49%; the reverse shorts won 29-33% of the time. (The hedge row's short win share printed then was
miscounted, fixed since.)"""
import json, os, sys, statistics as S
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import rule_fix_test as R
from rule_fix_test import DAY, DAILY, END, START, TAG, FEE, SLIP, LEV, MIN_NOTIONAL, prepare, line, STARTS, YEAR
from crypto_v1.github_worker import load_2h_strategy

TRAIL_S, SHORT_FRAC, MAX_BARS = 3.0, 0.4, 60


def run(D, mode, s0, s1, trail=8.0, maxpos=10, risk=0.01, quality=1.5, min_stop=0.03, pump=0.20, be=0.02, stats=None):
    BARS, ENT, SELL = D["bars"], D["ent"], D["sell"]
    cash, pos, spos, pend_buy, pend_sell, marks, curve = 170.0, {}, {}, [], set(), {}, []
    cnt = {"giris": 0, "kazanan": 0, "kapanan": 0, "short": 0, "short_kazanan": 0}
    day, day_eq, halted = None, None, False
    def notional():
        return (sum(p["qty"] * marks.get(s, p["entry"]) for s, p in pos.items())
                + sum(p["qty"] * marks.get(s, p["entry"]) for s, p in spos.items()))
    def equity():
        return cash + sum(p["qty"] * marks.get(s, p["entry"]) for s, p in pos.items()) \
                    - sum(p["qty"] * marks.get(s, p["entry"]) for s, p in spos.items())
    def close(s, price, stopped=False, t=None, f=None):
        nonlocal cash
        p = pos.pop(s); back = p["qty"] * price * (1 - SLIP) * (1 - FEE); cash += back
        cnt["kapanan"] += 1; cnt["kazanan"] += back > p["cost"]
        if mode == "hedge" and s in spos and spos[s].get("linked"):
            if stopped:                              # the short opened with the long stays, now on its own
                spos[s].update(linked=False, stop=p["entry"], low=f[2] if f else price, t=t); cnt["short"] += 1
            else:                                    # the long closed for another reason: its short goes too
                sclose(s, price)
        elif stopped and mode == "reverse" and f is not None and f[4]:
            q = p["qty"] * SHORT_FRAC; px = price * (1 - SLIP); proceeds = q * px * (1 - FEE); cash += proceeds
            spos[s] = dict(qty=q, entry=px, stop=p["entry"], low=f[2], t=t, cash_in=proceeds, linked=False)
            cnt["short"] += 1
    def sclose(s, price):
        nonlocal cash
        p = spos.pop(s); paid = p["qty"] * price * (1 + SLIP) * (1 + FEE); cash -= paid
        if not p.get("linked"):                      # only the shorts left running after a stop are counted
            cnt["short_kazanan"] += p["cash_in"] > paid
    for t in D["times"]:
        d = t // DAY * DAY
        if t // DAY != day:
            day, day_eq, halted = t // DAY, equity(), False
        for s in list(pend_sell):
            f = BARS.get(s, {}).get(t)
            if s in pos and f: close(s, f[0])
        pend_sell = set()
        eq = equity()
        allowed = DAILY.get(d, set()) if s0 <= d < s1 else set()
        for s, stop, score, breaks, pmp in sorted(pend_buy, key=lambda x: (-x[2], x[0])):
            if halted or len(pos) >= maxpos: break
            f = BARS.get(s, {}).get(t)
            if not f or s in pos or s not in allowed or pmp > pump: continue
            if s in spos: sclose(s, f[0])            # one-way account: the short goes, the long comes back
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
            if mode == "hedge" and notional() + qty * entry * (1 + SHORT_FRAC) > LEV * eq: continue
            cash -= qty * entry * (1 + FEE); cnt["giris"] += 1
            pos[s] = dict(qty=qty, entry=entry, stop=stop, unit=unit, high=entry, cost=qty * entry * (1 + FEE))
            if mode == "hedge":                       # the 2x short opened together with the 5x long
                q = qty * SHORT_FRAC; px = f[0] * (1 - SLIP); proceeds = q * px * (1 - FEE); cash += proceeds
                spos[s] = dict(qty=q, entry=px, stop=float("inf"), low=f[2], t=t, cash_in=proceeds, linked=True)
        pend_buy = []
        for s, p in list(spos.items()):
            f = BARS.get(s, {}).get(t)
            if not f: continue
            marks[s] = f[3]
            if p.get("linked"): continue
            if f[1] >= p["stop"]: sclose(s, max(f[0], p["stop"]))
            elif (t - p["t"]) // (D["iv"]) >= MAX_BARS: sclose(s, f[3])
            else:
                p["low"] = min(p["low"], f[2])
                if f[4]: p["stop"] = min(p["stop"], p["low"] + TRAIL_S * f[4])
        for s, p in list(pos.items()):
            f = BARS.get(s, {}).get(t)
            if f and f[2] <= p["stop"]:
                px = min(f[0], p["stop"])
                if stats is not None and px < p["entry"]:
                    fut = [BARS[s].get(t + k * D["iv"]) for k in range(1, 25)]
                    fut = [x for x in fut if x]
                    further = next((k for k, x in enumerate(fut) if x[2] <= px * 0.95), None)
                    back = next((k for k, x in enumerate(fut) if x[1] >= p["entry"]), None)
                    stats.append("devam" if further is not None and (back is None or further < back)
                                 else "geri dondu" if back is not None else "yatay")
                close(s, px, stopped=px < p["entry"], t=t, f=f)
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
        curve.append((t + D["iv"], e))
        if e <= 1.0: break
    return curve, cnt


def windows(D, mode):
    rets, cnts = [], []
    for s0 in STARTS:
        c, cnt = run(D, mode, s0, s0 + YEAR)
        w = [(t, e) for t, e in c if s0 <= t <= s0 + YEAR] or [(s0, 170.0)]
        rets.append(w[-1][1] / 170 - 1); cnts.append(cnt)
    st = {"hafta": S.mean(c["giris"] for c in cnts) / 52.14,
          "kazanan": sum(c["kazanan"] for c in cnts) / max(1, sum(c["kapanan"] for c in cnts))}
    shorts = sum(c["short"] for c in cnts); swin = sum(c["short_kazanan"] for c in cnts)
    return rets, st, shorts / len(cnts), (swin / shorts if shorts else 0)


if __name__ == "__main__":
    D = prepare("2h", dict(load_2h_strategy(relax=False), entry_mode="breakout"), 3.0)
    stats = []
    run(D, "long", START, END, stats=stats)
    n = len(stats)
    print(f"\n{TAG}: bot stop olduktan sonra (sonraki 48 saat): {n} stop | dusus %5 daha devam etti %{100*stats.count('devam')/n:.0f} | "
          f"once girise geri dondu %{100*stats.count('geri dondu')/n:.0f} | ikisi de olmadi %{100*stats.count('yatay')/n:.0f}\n", flush=True)
    for name, mode in (("1) sadece long (bugun)", "long"), ("2) sizin: 5x long + 2x short", "hedge"),
                       ("3) stop olunca short'a don", "reverse")):
        rets, st, shorts, swin = windows(D, mode)
        print(R.line(name, rets, st) + f" | yilda {shorts:.0f} short, karli %{100*swin:.0f}", flush=True)
    print("BITTI")
