"""PYRAMIDING test (29 Sep, the user's aggressive-sleeve idea #3): add to winners. Live 2H rules (breakout only,
Ichimoku, BTC>EMA50, 200-day regime), one 2H system, 170 USDT, max 10 symbols, 4x aggregate margin cap, risk 1%,
realistic costs, point-in-time daily top-50, both datasets, full path + 12-month windows starting every month.
Adds are decided on a bar CLOSE and filled at the next bar's open (like entries).
  yok        no adds (reference = live)
  R_tam_2    add a full unit at +1R, +2R, +3R (R of the first unit); whole stop >= last add - 2 ATR
  R_tam_4    same, whole stop >= last add - 4 ATR (more room)
  R_yarim_4  half-size adds at +1R/+2R/+3R, stop >= last add - 4 ATR
  R_2_4      full adds only at +2R and +4R, stop >= last add - 4 ATR
  turtle     add a full unit every +1 ATR above the last add, max 3 adds, stop >= last add - 2 ATR
The 8-ATR trailing stop after +1R stays in force for the whole position."""
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
MIN_NOTIONAL = {"ETHUSDT": 20.0, "BCHUSDT": 20.0, "LTCUSDT": 20.0, "BTCUSDT": 100.0}
os.chdir(REPO)
CFG = dict(load_2h_strategy(relax=False), entry_mode="breakout")
daily = {int(k): set(v) for k, v in json.loads((PIT / "daily_top50.json").read_text()).items()}
ever = sorted(set().union(*daily.values())); START = min(daily); END = max(daily) + DAY
t0 = time.time()
raw = json.loads((PIT / "data_2h.json").read_text(encoding="utf-8"))
btc_rows = rv5.symmetric_features(raw.pop("BTCUSDT"), CFG)
btc = {r["t"]: r for r in btc_rows}
BARS = {"BTCUSDT": {r["t"]: (r["o"], r["h"], r["l"], r["c"], r.get("atr")) for r in btc_rows if r["t"] >= START - 30 * DAY}}
ENT, SELL = defaultdict(list), {}
for s in list(raw):
    rows = raw.pop(s)
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

# ETH/BTC > EMA50 on daily closes known the next day (last 2H bar of each UTC day only)
def _daily(sym):
    m = {}
    for t in sorted(BARS[sym]):
        if (t + H2) % DAY == 0:
            m[(t + H2) // DAY * DAY] = BARS[sym][t][3]
    return m
_e, _b = _daily("ETHUSDT"), _daily("BTCUSDT")
_r = {d: _e[d] / _b[d] for d in _e if d in _b}
ETHOK, _m = {}, None
for d in sorted(_r):
    _m = _r[d] if _m is None else _m + (_r[d] - _m) * 2 / 51
    ETHOK[d] = _r[d] > _m
print(f"{TAG}: hazir {round(time.time()-t0)} sn", flush=True)

V = {"yok": None,
     "R_tam_4": dict(kind="R", levels=(1, 2, 3), size=1.0, gap=4.0),
     "R_2_4": dict(kind="R", levels=(2, 4), size=1.0, gap=4.0)}


def run(pyr, s0=START, s1=END, risk=0.01, trail=8.0, maxpos=10, quality=1.5, gate=False):
    cash, pos, pend_buy, pend_sell, pend_add, marks, curve, trades = 170.0, {}, [], set(), set(), {}, [], []
    day, day_eq, halted = None, None, False
    def equity():
        return cash + sum(p["qty"] * marks.get(s, p["entry0"]) for s, p in pos.items())
    def notional():
        return sum(p["qty"] * marks.get(s, p["entry0"]) for s, p in pos.items())
    def close(s, price, t):
        nonlocal cash
        p = pos.pop(s); px = price * (1 - SLIP)
        cash += p["qty"] * px * (1 - FEE)
        pnl = sum(q * (px * (1 - FEE) - e * (1 + FEE)) for q, e in p["lots"])
        trades.append((s, pnl / p["risk0"], p["t_in"], t, pnl, len(p["lots"]) - 1))
    for t in times:
        if t // DAY != day:
            day, day_eq, halted = t // DAY, equity(), False
        for s in list(pend_sell):
            f = BARS.get(s, {}).get(t)
            if s in pos and f:
                close(s, f[0], t)
        pend_sell = set()
        eq = equity(); d = t // DAY * DAY
        # pyramid adds (decided on the previous close, filled at this open)
        for s in list(pend_add):
            p = pos.get(s); f = BARS.get(s, {}).get(t)
            if not p or not f or halted:
                continue
            px = f[0] * (1 + SLIP); q = p["q0"] * pyr["size"]
            if q * px < MIN_NOTIONAL.get(s, 5.0) * 1.03 or notional() + q * px > LEV * eq:
                continue
            cash -= q * px * (1 + FEE)
            p["lots"].append((q, px)); p["qty"] += q; p["adds"] += 1; p["last_add"] = px
            if f[4]:
                p["stop"] = max(p["stop"], px - pyr["gap"] * f[4])
        pend_add = set()
        allowed = daily.get(d, set()) if s0 <= d < s1 and (not gate or ETHOK.get(d, False)) else set()
        for s, stop, score, breaks in sorted(pend_buy, key=lambda x: (-x[2], x[0])):
            if halted or len(pos) >= maxpos:
                break
            f = BARS.get(s, {}).get(t)
            if not f or s in pos or s not in allowed:
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
                          risk0=qty * unit, high=entry, t_in=t, adds=0, last_add=entry)
        pend_buy = []
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
            if pyr and s not in pend_sell and f[4]:
                if pyr["kind"] == "R":
                    lv = pyr["levels"]
                    if p["adds"] < len(lv) and f[3] >= p["entry0"] + lv[p["adds"]] * p["unit"]:
                        pend_add.add(s)
                elif p["adds"] < pyr["max_adds"] and f[3] >= p["last_add"] + pyr["step"] * f[4]:
                    pend_add.add(s)
        if not halted:
            pend_buy = [x for x in ENT.get(t, []) if x[0] not in pos]
        e = equity()
        if e <= day_eq * 0.9 and not halted:
            halted = True; pend_sell |= set(pos)
        curve.append((t + H2, e))
        if e <= 1.0:
            break
    return curve, trades


ts = lambda y, m: int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)
starts = ([ts(2021 + (m - 1) // 12, (m - 1) % 12 + 1) for m in range(1, 22)] if TAG == "2021-23"
          else [ts(2023 + (m - 1) // 12, (m - 1) % 12 + 1) for m in range(9, 33)])
YEAR = 365 * DAY
def dd(c):
    pk, m = c[0][1], 0.0
    for _, e in c:
        pk = max(pk, e); m = max(m, 1 - e / pk)
    return m
print(f"{'':10} | {'TUM DONEM':>9} {'dusus':>6} {'5R+':>4} {'en buyuk R':>10} | 12 ay: {'medyan':>7} {'kotu%10':>7} {'en kotu':>7} {'en iyi':>7} {'zarar%':>6} {'dusus med/max':>13} {'batan':>5}", flush=True)
out = {}
for k, pyr in V.items():
  for gate in (False, True):
      c, tr = run(pyr, gate=gate)
      rets, dds = [], []
      for s0 in starts:
          cc, _ = run(pyr, s0, s0 + YEAR, gate=gate)
          w = [(t, e) for t, e in cc if s0 <= t <= s0 + YEAR] or [(s0, 170.0)]
          rets.append(w[-1][1] / 170 - 1); dds.append(dd(w))
      sr = sorted(rets)
      out[k + (" +ETH/BTC" if gate else "")] = dict(full=c[-1][1] / 170, full_dd=dd(c), rets=rets, dds=dds)
      print(f"{k + (' +ETH/BTC' if gate else ''):18} | {c[-1][1]/170:8.2f}x %{100*dd(c):4.0f} {sum(x[1]>=5 for x in tr):4d} {max(x[1] for x in tr):10.1f} | "
            f"{100*S.median(rets):+6.0f}% {100*sr[len(sr)//10]:+6.0f}% {100*sr[0]:+6.0f}% {100*sr[-1]:+6.0f}% "
            f"%{100*sum(x<0 for x in rets)/len(rets):4.0f}   %{100*S.median(dds):3.0f}/%{100*max(dds):3.0f} "
            f"{sum(x <= -0.9 for x in rets):5d}", flush=True)
json.dump(out, open(Path(__file__).parent / f"pit_pyramid2_{TAG}.json", "w"))
print("BITTI", flush=True)
