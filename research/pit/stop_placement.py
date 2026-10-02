"""Where should the stop sit? (2 Oct 2026). After a 2H long is stopped, the price came back to the entry first in
48-49% of cases (stop_and_reverse.py): many stops are shake-outs, not real drops. The live 2H system (8-ATR trail,
10 positions, 1% risk with quality sizing, pump filter, break-even at +2%, 3% minimum stop), 12-month windows
starting every month in both eras, point-in-time top-50, realistic costs. Stop variants:
  3 ATR (today)       intrabar stop 3 ATR under the signal close
  4 ATR / 5 ATR       wider intrabar stops (risk per trade unchanged, so the position is smaller)
  close-based         exits only when a 2H candle CLOSES under the 3-ATR level (wicks ignored), with an emergency
                      intrabar stop 6 ATR under
  swing low           under the lowest low of the 10 bars before the entry minus 0.5 ATR (3%..15% away)
  re-entry            3 ATR, and after a stop the coin is bought again if a 2H candle closes back above the
                      first entry within 24 hours (new stop: the lowest low since the stop - 0.5 ATR)"""
import json, os, sys, statistics as S
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import rule_fix_test as R
from rule_fix_test import DAY, DAILY, TAG, FEE, SLIP, LEV, MIN_NOTIONAL, prepare, STARTS, YEAR
from crypto_v1.github_worker import load_2h_strategy


def run(D, s0, s1, mode="atr", mult=3.0, trail=8.0, maxpos=10, risk=0.01, quality=1.5, min_stop=0.03, pump=0.20, be=0.02):
    BARS, ENT, SELL, IV = D["bars"], D["ent"], D["sell"], D["iv"]
    cash, pos, pend_buy, pend_sell, marks, curve, rewatch = 170.0, {}, [], set(), {}, [], {}
    cnt = {"giris": 0, "kazanan": 0, "kapanan": 0, "stop": 0, "tekrar": 0}
    day, day_eq, halted = None, None, False
    def notional(): return sum(p["qty"] * marks.get(s, p["entry"]) for s, p in pos.items())
    def equity(): return cash + notional()
    def close(s, price, stopped=False, t=None):
        nonlocal cash
        p = pos.pop(s); back = p["qty"] * price * (1 - SLIP) * (1 - FEE); cash += back
        cnt["kapanan"] += 1; cnt["kazanan"] += back > p["cost"]
        if stopped and price < p["entry"]:
            cnt["stop"] += 1
            if mode == "reentry":
                rewatch[s] = dict(level=p["entry0"], until=t + 12 * IV, low=price)
    def open_long(s, f, stop, breaks, eq, entry0=None):
        nonlocal cash
        entry = f[0] * (1 + SLIP)
        stop = min(stop, entry * (1 - min_stop))
        if stop <= 0 or entry <= stop: return False
        unit = entry * (1 + FEE) - stop * (1 - SLIP) * (1 - FEE)
        r = risk * (quality if breaks >= 1 else 0.5)
        qty = min(eq * r / unit, LEV * eq / (entry * (1 + FEE)))
        mn = MIN_NOTIONAL.get(s, 5.0) * 1.03
        if qty * entry < mn:
            if (mn / entry) * unit > eq * r: return False
            qty = mn / entry
        if notional() + qty * entry > LEV * eq: return False
        cash -= qty * entry * (1 + FEE); cnt["giris"] += 1
        atr = f[4] or (entry - stop) / 3
        pos[s] = dict(qty=qty, entry=entry, entry0=entry0 or entry, stop=stop, unit=unit, high=entry,
                      cost=qty * entry * (1 + FEE), close_stop=stop, hard=entry - 6 * atr if mode == "close" else None)
        if mode == "close":
            pos[s]["stop"] = max(pos[s]["hard"], 1e-12)
        return True
    for t in D["times"]:
        d = t // DAY * DAY
        if t // DAY != day: day, day_eq, halted = t // DAY, equity(), False
        for s in list(pend_sell):
            f = BARS.get(s, {}).get(t)
            if s in pos and f: close(s, f[0])
        pend_sell = set(); eq = equity()
        allowed = DAILY.get(d, set()) if s0 <= d < s1 else set()
        for (s, stop, score, breaks, pmp), t_sig in sorted(pend_buy, key=lambda x: (-x[0][2], x[0][0])):
            if halted or len(pos) >= maxpos: break
            f = BARS.get(s, {}).get(t)
            if not f or s in pos or s not in allowed or pmp > pump: continue
            sig = BARS[s].get(t_sig)
            if sig and sig[4]:
                c, atr = sig[3], sig[4]
                if mode == "atr": stop = c - mult * atr
                elif mode == "swing":
                    lows = [BARS[s][t_sig - k * IV][2] for k in range(10) if (t_sig - k * IV) in BARS[s]]
                    stop = min(lows) - 0.5 * atr
                    stop = max(min(stop, f[0] * 0.97), f[0] * 0.85)
                else: stop = c - 3.0 * atr
            if open_long(s, f, stop, breaks, eq): rewatch.pop(s, None)
        pend_buy = []
        # re-entry: a close back above the first entry within 24 hours of the stop
        for s, w in list(rewatch.items()):
            f = BARS.get(s, {}).get(t)
            if not f or t > w["until"] or s in pos:
                if f and t > w["until"]: rewatch.pop(s, None)
                continue
            w["low"] = min(w["low"], f[2])
            if f[3] > w["level"] and s in allowed and not halted and len(pos) < maxpos:
                nxt = BARS[s].get(t + IV)
                if nxt and open_long(s, nxt, w["low"] - 0.5 * (f[4] or 0), 1, equity(), entry0=w["level"]):
                    cnt["tekrar"] += 1
                rewatch.pop(s, None)
        for s, p in list(pos.items()):
            f = BARS.get(s, {}).get(t)
            if not f: continue
            if f[2] <= p["stop"]: close(s, min(f[0], p["stop"]), stopped=True, t=t)
            elif mode == "close" and f[3] <= p["close_stop"]: close(s, f[3], stopped=True, t=t)
        for s, p in pos.items():
            f = BARS.get(s, {}).get(t)
            if not f: continue
            marks[s] = f[3]
            if t in SELL.get(s, ()): pend_sell.add(s)
            p["high"] = max(p["high"], f[1])
            if p["high"] >= p["entry"] + p["unit"] and f[4]:
                level = p["high"] - trail * f[4]
                p["stop"] = max(p["stop"], level); p["close_stop"] = max(p["close_stop"], level)
            if be and p["high"] >= p["entry"] * (1 + be):
                level = p["entry"] * (1 + 2 * FEE + 2 * SLIP)
                p["stop"] = max(p["stop"], level); p["close_stop"] = max(p["close_stop"], level)
        if not halted: pend_buy = [(x, t) for x in ENT.get(t, []) if x[0] not in pos]
        e = equity()
        if e <= day_eq * 0.9 and not halted: halted = True; pend_sell |= set(pos)
        curve.append((t + IV, e))
        if e <= 1.0: break
    return curve, cnt


def windows(D, **kw):
    rets, cnts = [], []
    for s0 in STARTS:
        c, cnt = run(D, s0, s0 + YEAR, **kw)
        w = [(t, e) for t, e in c if s0 <= t <= s0 + YEAR] or [(s0, 170.0)]
        rets.append(w[-1][1] / 170 - 1); cnts.append(cnt)
    tot = lambda k: sum(c[k] for c in cnts)
    st = {"hafta": S.mean(c["giris"] for c in cnts) / 52.14, "kazanan": tot("kazanan") / max(1, tot("kapanan"))}
    return rets, st, tot("stop") / len(cnts), tot("tekrar") / len(cnts)


if __name__ == "__main__":
    D = prepare("2h", dict(load_2h_strategy(relax=False), entry_mode="breakout"), 3.0)
    print(flush=True)
    for name, kw in (("3 ATR (bugun)", dict(mode="atr", mult=3.0)), ("4 ATR", dict(mode="atr", mult=4.0)),
                     ("5 ATR", dict(mode="atr", mult=5.0)), ("kapanisa gore stop", dict(mode="close")),
                     ("son 10 mumun dibi", dict(mode="swing")), ("stop + 24 saatte geri alim", dict(mode="reentry"))):
        rets, st, stops, again = windows(D, **kw)
        print(R.line(name, rets, st) + f" | yilda {stops:.0f} zararli stop, {again:.0f} geri alim", flush=True)
    print("BITTI")
