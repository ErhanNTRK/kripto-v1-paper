"""The 70% aggressive sleeve as the user wants it (29 Sep): 2H trend rules + the dip-catcher sharing ONE pot,
optionally with sparse pyramiding. One 170 USDT pot, 4x aggregate margin cap, realistic costs, point-in-time
top-50, data cut at symbol re-use / redenomination jumps, both eras, full path + 12-month windows.
Dip-catcher: at each UTC day start, resting limit buys on the day's top-10 coins (rank order) at yesterday's close
x 0.85 (skipping coins the trend side holds); each 10% of pot equity; ALL may fill in the same bar; exit +8% take
profit (not on the fill bar), -25% stop, else the close one day after the fill. The trend side skips coins the dip
side holds (one-way account)."""
import os, sys, json, time, statistics as S
from collections import defaultdict
from pathlib import Path
from datetime import datetime, timezone
REPO = "C:/Users/ASUS-PC/Documents/GitHub/kripto-v1-paper"; sys.path.insert(0, REPO)
import crypto_v1.research_v5 as rv5
from crypto_v1.research_v5 import ShortWindowLongModel as M
from crypto_v1.github_worker import load_2h_strategy
PIT = Path(os.environ["PIT_DIR"]); TAG = "2021-23" if "pit2020" in str(PIT) else "2023-26"
DAY, H2 = 86_400_000, 2 * 3600 * 1000
FEE, SLIP, LEV = 0.0005, 0.0002, 4
DIP_COST = 0.0007
MIN_NOTIONAL = {"ETHUSDT": 20.0, "BCHUSDT": 20.0, "LTCUSDT": 20.0, "BTCUSDT": 100.0}
os.chdir(REPO)
CFG = dict(load_2h_strategy(relax=False), entry_mode="breakout")
ORDERED = {int(k): [s for s in v if s != "BTCUSDT"] for k, v in json.loads((PIT / "daily_top50.json").read_text()).items()}
daily = {d: set(v) for d, v in ORDERED.items()}
ever = sorted(set().union(*daily.values())); START = min(daily); END = max(daily) + DAY
t0 = time.time()
raw = json.loads((PIT / "data_2h.json").read_text(encoding="utf-8"))

def cut(rows):
    keep = rows[:1]
    for a, b in zip(rows, rows[1:]):
        if a["c"] > 0 and (b["o"] / a["c"] > 3 or b["o"] / a["c"] < 1 / 3 or b["t"] - a["t"] > 3 * DAY):
            break
        keep.append(b)
    return keep

btc_rows = rv5.symmetric_features(raw.pop("BTCUSDT"), CFG)
btc = {r["t"]: r for r in btc_rows}
BARS = {"BTCUSDT": {r["t"]: (r["o"], r["h"], r["l"], r["c"], r.get("atr")) for r in btc_rows if r["t"] >= START - 30 * DAY}}
ENT, SELL = defaultdict(list), {}
for s in list(raw):
    rows = cut(raw.pop(s))
    if s not in ever or len(rows) < 250:
        continue
    b, sl = {}, set()
    for f in rv5.symmetric_features(rows, CFG):
        t = f["t"]
        if t < START - 30 * DAY:
            continue
        b[t] = (f["o"], f["h"], f["l"], f["c"], f.get("atr"))
        bt = btc.get(t)
        if bt is None:
            continue
        if M.sell(f, bt, CFG):
            sl.add(t)
        if M.buy(f, bt, CFG):
            vr = f["v"] / f["volume_avg"] if f.get("volume_avg") else 0
            ENT[t].append((s, M.stop(f, CFG), f.get("signal_score", vr), f.get("breaks_up", 0)))
    BARS[s], SELL[s] = b, sl
del raw
times = sorted(t for t in BARS["BTCUSDT"] if START <= t < END)
print(f"{TAG}: hazir {round(time.time()-t0)} sn", flush=True)
PYR = dict(levels=(2, 4), size=1.0, gap=4.0)


def run(trend=True, dip=False, pyr=None, s0=START, s1=END, risk=0.01, trail=8.0, maxpos=10, quality=1.5,
        dip_n=10, dip_size=0.10, dip_depth=0.15, dip_tp=0.08, dip_stop=0.25, dip_days=1):
    cash, pos, dpos = 170.0, {}, {}
    pend_buy, pend_sell, pend_add, marks, curve = [], set(), set(), {}, []
    stats = {"trend": 0.0, "dip": 0.0, "dip_n": 0}
    day, day_eq, halted = None, None, False
    orders = {}   # dip limit orders for the day: symbol -> limit price
    def notional():
        return (sum(p["qty"] * marks.get(s, p["entry0"]) for s, p in pos.items())
                + sum(p["qty"] * marks.get(s, p["entry"]) for s, p in dpos.items()))
    def equity():
        return cash + notional()
    def close(s, price, t):
        nonlocal cash
        p = pos.pop(s); px = price * (1 - SLIP)
        cash += p["qty"] * px * (1 - FEE)
        stats["trend"] += sum(q * (px * (1 - FEE) - e * (1 + FEE)) for q, e in p["lots"])
    def dclose(s, price):
        nonlocal cash
        p = dpos.pop(s); out = price * (1 - DIP_COST)
        cash += p["qty"] * out
        stats["dip"] += p["qty"] * (out - p["entry"])
    for t in times:
        d = t // DAY * DAY
        if t // DAY != day:
            day, day_eq, halted = t // DAY, equity(), False
            orders = {}
            if dip and s0 <= d < s1:
                for s in ORDERED.get(d, [])[:dip_n]:
                    prev = BARS.get(s, {}).get(d - H2)
                    if prev and s not in pos and s not in dpos:
                        orders[s] = prev[3] * (1 - dip_depth)
        # --- trend side: pending sells / adds / buys at this bar's open ---
        for s in list(pend_sell):
            f = BARS.get(s, {}).get(t)
            if s in pos and f:
                close(s, f[0], t)
        pend_sell = set()
        eq = equity()
        for s in list(pend_add):
            p = pos.get(s); f = BARS.get(s, {}).get(t)
            if not p or not f or halted:
                continue
            px = f[0] * (1 + SLIP); q = p["q0"] * pyr["size"]
            if q * px < MIN_NOTIONAL.get(s, 5.0) * 1.03 or notional() + q * px > LEV * eq:
                continue
            cash -= q * px * (1 + FEE)
            p["lots"].append((q, px)); p["qty"] += q; p["adds"] += 1
            if f[4]:
                p["stop"] = max(p["stop"], px - pyr["gap"] * f[4])
        pend_add = set()
        allowed = daily.get(d, set()) if s0 <= d < s1 else set()
        for s, stop, score, breaks in sorted(pend_buy, key=lambda x: (-x[2], x[0])):
            if halted or len(pos) >= maxpos:
                break
            f = BARS.get(s, {}).get(t)
            if not f or s in pos or s in dpos or s not in allowed:
                continue
            entry = f[0] * (1 + SLIP)
            if stop <= 0 or entry <= stop:
                continue
            unit = entry * (1 + FEE) - stop * (1 - SLIP) * (1 - FEE)
            r = risk * (quality if breaks >= 1 else 0.5)
            qty = min(eq * r / unit, LEV * eq / (entry * (1 + FEE)))
            mn = MIN_NOTIONAL.get(s, 5.0) * 1.03
            if qty * entry < mn:
                if (mn / entry) * unit > eq * r:
                    continue
                qty = mn / entry
            if notional() + qty * entry > LEV * eq:
                continue
            cash -= qty * entry * (1 + FEE)
            pos[s] = dict(qty=qty, q0=qty, entry0=entry, lots=[(qty, entry)], stop=stop, stop0=stop, unit=unit,
                          high=entry, adds=0)
            orders.pop(s, None)
        pend_buy = []
        # --- dip side: exits of open dip positions, then fills of resting orders (all can fill at once) ---
        for s, p in list(dpos.items()):
            f = BARS.get(s, {}).get(t)
            if not f:
                continue
            marks[s] = f[3]
            if f[2] <= p["stop"]:
                dclose(s, min(f[0], p["stop"]))
            elif f[1] >= p["tp"]:
                dclose(s, max(f[0], p["tp"]))
            elif t >= p["until"]:
                dclose(s, f[3])
        for s, limit in list(orders.items()):
            f = BARS.get(s, {}).get(t)
            if not f or f[2] > limit or s in pos or s in dpos:
                continue
            del orders[s]
            px = min(limit, f[0]) * (1 + DIP_COST)
            qty = dip_size * eq / px
            if qty * px < MIN_NOTIONAL.get(s, 5.0) * 1.03 or notional() + qty * px > LEV * eq:
                continue
            cash -= qty * px; stats["dip_n"] += 1
            dpos[s] = dict(qty=qty, entry=px, tp=px * (1 + dip_tp), stop=px * (1 - dip_stop), until=t + dip_days * DAY)
            marks[s] = f[3]
            if f[2] <= px * (1 - dip_stop):          # the same bar went on to the stop: conservative
                dclose(s, px * (1 - dip_stop))
        # --- trend side: stops, marks, signals ---
        for s, p in list(pos.items()):
            f = BARS.get(s, {}).get(t)
            if f and f[2] <= p["stop"]:
                close(s, min(f[0], p["stop"]), t)
        for s, p in pos.items():
            f = BARS.get(s, {}).get(t)
            if not f:
                continue
            marks[s] = f[3]
            if t in SELL.get(s, ()):
                pend_sell.add(s)
            p["high"] = max(p["high"], f[1])
            if p["high"] >= p["entry0"] + p["unit"] and f[4]:
                p["stop"] = max(p["stop"], p["high"] - trail * f[4])
            if pyr and s not in pend_sell and f[4] and p["adds"] < len(pyr["levels"]) \
                    and f[3] >= p["entry0"] + pyr["levels"][p["adds"]] * p["unit"]:
                pend_add.add(s)
        if trend and not halted:
            pend_buy = [x for x in ENT.get(t, []) if x[0] not in pos]
        e = equity()
        if e <= day_eq * 0.9 and not halted:
            halted = True; pend_sell |= set(pos)
            for s in list(dpos):
                f = BARS.get(s, {}).get(t)
                if f:
                    dclose(s, f[3])
            orders = {}
        curve.append((t + H2, e))
        if e <= 1.0:
            break
    return curve, stats


ts = lambda y, m: int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)
starts = ([ts(2021 + (m - 1) // 12, (m - 1) % 12 + 1) for m in range(1, 22)] if TAG == "2021-23"
          else [ts(2023 + (m - 1) // 12, (m - 1) % 12 + 1) for m in range(9, 33)])
YEAR = 365 * DAY
def dd(c):
    pk, m = c[0][1], 0.0
    for _, e in c:
        pk = max(pk, e); m = max(m, 1 - e / pk)
    return m
VARS = [("sadece trend (bugunku 2H)", dict()),
        ("trend + igne", dict(dip=True)),
        ("trend + piramit", dict(pyr=PYR)),
        ("trend + piramit + igne", dict(pyr=PYR, dip=True)),
        ("sadece igne", dict(trend=False, dip=True))]
for name, kw in VARS:
    c, st = run(**kw)
    rets, dds = [], []
    for s0 in starts:
        cc, _ = run(s0=s0, s1=s0 + YEAR, **kw)
        w = [(t, e) for t, e in cc if s0 <= t <= s0 + YEAR] or [(s0, 170.0)]
        rets.append(w[-1][1] / 170 - 1); dds.append(dd(w))
    sr = sorted(rets)
    print(f"{name:28} | {c[-1][1]/170:7.2f}x dusus %{100*dd(c):3.0f} | trend kar {st['trend']:+9.0f} igne kar {st['dip']:+8.0f} ({st['dip_n']} dolum) | "
          f"12 ay medyan {100*S.median(rets):+5.0f}% en kotu {100*sr[0]:+5.0f}% en iyi {100*sr[-1]:+6.0f}% zararli %{100*sum(x<0 for x in rets)/len(rets):3.0f} "
          f"dusus med/max %{100*S.median(dds):3.0f}/%{100*max(dds):3.0f}", flush=True)
print("BITTI", flush=True)
