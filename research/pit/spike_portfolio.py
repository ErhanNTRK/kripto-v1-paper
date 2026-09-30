"""Portfolio test of the user's spike-then-red rule (30 Sep 2026) inside the 2H sleeve, next to the 2H trend
rules (3-ATR stop), sharing one pot (one position per coin, trend first when both fire on a bar), both eras,
12-month windows starting every month + full path. Spike entries: 2H big green candle making a 50-bar high,
next bar red; BTC > EMA50 and the coin's Ichimoku fully bullish at the red candle; buy at the next open;
2-ATR stop; out after 24 hours (12 bars). Size: the trend rules' 1% x 1.5 risk sizing.
Variants: A big>=3ATR & red>=1ATR, B big>=3ATR any red, C big>=2ATR & red>=1ATR."""
import os, sys, json, statistics as S
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
HERE = Path(__file__).parent
src = (HERE / "package_test.py").read_text(encoding="utf-8").split("PYR = dict(")[0]
ns = {"__file__": str(HERE / "package_test.py")}
exec(compile(src, "package_core", "exec"), ns)
rv5, DAY, TAG = ns["rv5"], ns["DAY"], ns["TAG"]
PIT, DAILY, START, END = ns["PIT"], ns["DAILY"], ns["START"], ns["END"]
cfg2 = dict(ns["load_2h_strategy"](relax=False), entry_mode="breakout")
D = ns["prepare"]("2h", cfg2)

# spike events: signal time = the red candle's bar time; entry at the next bar open
raw = json.loads((PIT / "data_2h.json").read_text(encoding="utf-8"))
fc = dict(cfg2, entry_mode="breakout+momentum")
btc = {r["t"]: r for r in rv5.symmetric_features(raw.pop("BTCUSDT"), fc)}
SPIKE = {k: defaultdict(list) for k in "ABC"}
for s in list(raw):
    rows = ns["cut"](raw.pop(s))
    if s not in ns["EVER"] or len(rows) < 120:
        continue
    f = rv5.symmetric_features(rows, fc)
    for i in range(51, len(f) - 1):
        big, red = f[i - 1], f[i]
        if not big.get("atr") or red["c"] >= red["o"]:
            continue
        body = big["c"] - big["o"]
        if body < 2 * big["atr"] or big["h"] < max(x["h"] for x in f[i - 51:i - 1]):
            continue
        bt = btc.get(red["t"])
        if not (bt and bt.get("ema50") and bt["c"] > bt["ema50"] and red.get("ichimoku_ok") is True):
            continue
        redbody = red["o"] - red["c"]
        stop = red["c"] - 2 * big["atr"]
        if body >= 3 * big["atr"] and redbody >= big["atr"]:
            SPIKE["A"][red["t"]].append((s, stop))
        if body >= 3 * big["atr"]:
            SPIKE["B"][red["t"]].append((s, stop))
        if redbody >= big["atr"]:
            SPIKE["C"][red["t"]].append((s, stop))
del raw
print(f"{TAG}: spike olay sayisi " + ", ".join(f"{k} {sum(len(v) for v in SPIKE[k].values())}" for k in "ABC"), flush=True)

FEE, SLIP, LEV, MIN_NOTIONAL = ns["FEE"], ns["SLIP"], ns["LEV"], ns["MIN_NOTIONAL"]

def run(variant=None, trend=True, s0=START, s1=END, risk=0.01, quality=1.5, trail=8.0, maxpos=10, hold=12):
    BARS, ENT, SELL = D["bars"], D["ent"][3.0], D["sell"]
    cash, pos, sp, pend_buy, pend_sp, pend_sell, marks, curve = 170.0, {}, {}, [], [], set(), {}, []
    cnt = {"trend": 0, "spike": 0}
    day, day_eq, halted = None, None, False
    def notional():
        return sum(p["qty"] * marks.get(s, p["entry"]) for s, p in list(pos.items()) + list(sp.items()))
    def equity():
        return cash + notional()
    def size(eq, entry, stop):
        unit = entry * (1 + FEE) - stop * (1 - SLIP) * (1 - FEE)
        if stop <= 0 or unit <= 0:
            return None, None
        qty = min(eq * risk * quality / unit, LEV * eq / (entry * (1 + FEE)))
        return qty, unit
    for t in D["times"]:
        d = t // DAY * DAY
        if t // DAY != day:
            day, day_eq, halted = t // DAY, equity(), False
        for s in list(pend_sell):
            f = BARS.get(s, {}).get(t)
            if s in pos and f:
                p = pos.pop(s); cash += p["qty"] * f[0] * (1 - SLIP) * (1 - FEE)
        pend_sell = set()
        eq = equity()
        allowed = DAILY.get(d, set()) if s0 <= d < s1 else set()
        for s, stop, score, breaks in sorted(pend_buy, key=lambda x: (-x[2], x[0])):
            if halted or len(pos) + len(sp) >= maxpos:
                break
            f = BARS.get(s, {}).get(t)
            if not f or s in pos or s in sp or s not in allowed:
                continue
            entry = f[0] * (1 + SLIP)
            if entry <= stop:
                continue
            qty, unit = size(eq, entry, stop)
            if qty is None or qty * entry < MIN_NOTIONAL.get(s, 5.0) or notional() + qty * entry > LEV * eq:
                continue
            cash -= qty * entry * (1 + FEE); cnt["trend"] += 1
            pos[s] = dict(qty=qty, entry=entry, stop=stop, unit=unit, high=entry)
        for s, stop in pend_sp:
            if halted or len(pos) + len(sp) >= maxpos:
                break
            f = BARS.get(s, {}).get(t)
            if not f or s in pos or s in sp or s not in allowed:
                continue
            entry = f[0] * (1 + SLIP)
            if entry <= stop:
                continue
            qty, unit = size(eq, entry, stop)
            if qty is None or qty * entry < MIN_NOTIONAL.get(s, 5.0) or notional() + qty * entry > LEV * eq:
                continue
            cash -= qty * entry * (1 + FEE); cnt["spike"] += 1
            sp[s] = dict(qty=qty, entry=entry, stop=stop, until=t + hold * ns["run"].__defaults__[0] if False else t + hold * D["iv"])
        pend_buy, pend_sp = [], []
        for s, p in list(sp.items()):
            f = BARS.get(s, {}).get(t)
            if not f:
                continue
            marks[s] = f[3]
            if f[2] <= p["stop"]:
                cash += p["qty"] * min(f[0], p["stop"]) * (1 - SLIP) * (1 - FEE); del sp[s]
            elif t + D["iv"] >= p["until"]:
                cash += p["qty"] * f[3] * (1 - SLIP) * (1 - FEE); del sp[s]
        for s, p in list(pos.items()):
            f = BARS.get(s, {}).get(t)
            if f and f[2] <= p["stop"]:
                cash += p["qty"] * min(f[0], p["stop"]) * (1 - SLIP) * (1 - FEE); del pos[s]
        for s, p in pos.items():
            f = BARS.get(s, {}).get(t)
            if not f:
                continue
            marks[s] = f[3]
            if t in SELL.get(s, ()):
                pend_sell.add(s)
            p["high"] = max(p["high"], f[1])
            if p["high"] >= p["entry"] + p["unit"] and f[4]:
                p["stop"] = max(p["stop"], p["high"] - trail * f[4])
        if not halted:
            if trend:
                pend_buy = [x for x in ENT.get(t, []) if x[0] not in pos]
            if variant:
                pend_sp = [x for x in SPIKE[variant].get(t, []) if x[0] not in pos and x[0] not in sp]
        e = equity()
        if e <= day_eq * 0.9 and not halted:
            halted = True; pend_sell |= set(pos)
            for s in list(sp):
                f = BARS.get(s, {}).get(t)
                if f:
                    cash += sp[s]["qty"] * f[3] * (1 - SLIP) * (1 - FEE); del sp[s]
        curve.append((t + D["iv"], e))
        if e <= 1.0:
            break
    return curve, cnt

ts = lambda y, m: int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)
STARTS = ([ts(2021 + (m - 1) // 12, (m - 1) % 12 + 1) for m in range(1, 22)] if TAG == "2021-23"
          else [ts(2023 + (m - 1) // 12, (m - 1) % 12 + 1) for m in range(9, 33)])
YEAR = 365 * DAY
for name, kw in (("2H trend (bugun)", dict()), ("sadece spike A", dict(variant="A", trend=False)),
                 ("trend + spike A", dict(variant="A")), ("trend + spike B", dict(variant="B")),
                 ("trend + spike C", dict(variant="C"))):
    c, cnt = run(**kw)
    rets = []
    for s0 in STARTS:
        cc, _ = run(s0=s0, s1=s0 + YEAR, **kw)
        w = [(t, e) for t, e in cc if s0 <= t <= s0 + YEAR] or [(s0, 170.0)]
        rets.append(w[-1][1] / 170 - 1)
    sr = sorted(rets)
    weeks = (D["times"][-1] - D["times"][0]) / (7 * DAY)
    print(f"{name:20} | tum donem {c[-1][1]/170:6.2f}x | haftada trend {cnt['trend']/weeks:4.1f} spike {cnt['spike']/weeks:4.2f} | "
          f"100$ 12 ay: tipik {100*(1+S.median(rets)):5.0f}$ en kotu {100*(1+sr[0]):5.0f}$ en iyi {100*(1+sr[-1]):5.0f}$ "
          f"zararli %{100*sum(x<0 for x in rets)/len(rets):3.0f}", flush=True)
print("BITTI")
