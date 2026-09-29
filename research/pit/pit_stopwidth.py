"""AGGRESSIVE sleeve candidates (29 Sep, the user wants 65% in an aggressive method): one 2H system, its own
170 USDT, max 10 positions, point-in-time daily top-50, realistic costs, multi-start 12-month windows + full path.
Entry variants (exits = the same model's sell rule under that variant's config, 2-ATR stop, 8-ATR trail after +1R):
  canli         live 2H rules (reference)
  btc_hizli     BTC filter 'very_loose' (BTC > EMA20 instead of EMA50)
  btc_yok       BTC filter off (200-day regime kept)
  serbest       BTC off + Ichimoku off + no first-time-high skip + no extension cap (200-day regime kept)
  tam_serbest   'serbest' and the 200-day regime off too (trades bear markets)
  pompa         pump catcher: 2H bar volume >= 5x its 20-bar average, close breaks the Donchian high, bar +5% or
                more; no BTC / Ichimoku filters (200-day regime kept)
Risk per trade 1% / 2% / 3% (round-up to Binance minimum allowed up to the same risk)."""
import os, sys, json, math, time, statistics as S
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
BASE = dict(load_2h_strategy(relax=False), entry_mode="breakout")
V = {"stop 2 ATR (canli)": dict(BASE),
     "stop 2.5 ATR": dict(BASE, atr_multiplier=2.5),
     "stop 3 ATR": dict(BASE, atr_multiplier=3.0)}
daily = {int(k): set(v) for k, v in json.loads((PIT / "daily_top50.json").read_text()).items()}
ever = sorted(set().union(*daily.values())); START = min(daily); END = max(daily) + DAY
t0 = time.time()
raw = json.loads((PIT / "data_2h.json").read_text(encoding="utf-8"))
feat_cfg = dict(BASE, entry_mode="breakout+momentum")
btc_rows = rv5.symmetric_features(raw.pop("BTCUSDT"), feat_cfg)
btc = {r["t"]: r for r in btc_rows}
BARS = {"BTCUSDT": {r["t"]: (r["o"], r["h"], r["l"], r["c"], r.get("atr")) for r in btc_rows if r["t"] >= START - 30 * DAY}}
ENT = {k: defaultdict(list) for k in V}; SELL = {k: {} for k in V}
for s in list(raw):
    rows = raw.pop(s)
    if s not in ever or len(rows) < 250:
        continue
    b = {}; sl = {k: set() for k in V}
    for f in rv5.symmetric_features(rows, feat_cfg):
        t = f["t"]
        if t < START - 30 * DAY:
            continue
        b[t] = (f["o"], f["h"], f["l"], f["c"], f.get("atr"))
        bt = btc.get(t)
        if bt is None:
            continue
        for k, cfg in V.items():
            if M.sell(f, bt, cfg):
                sl[k].add(t)
            if False:
                ok = (f.get("atr") and f.get("volume_avg") and f["v"] >= 5 * f["volume_avg"] and f.get("breaks_up", 0) >= 1
                      and f["c"] >= 1.05 * f["o"] and bt.get("regime_ma") and bt["c"] >= bt["regime_ma"])
                if ok:
                    ENT[k][t].append((s, f["c"] - 2 * f["atr"], f["v"] / f["volume_avg"], f.get("breaks_up", 0)))
            elif M.buy(f, bt, cfg):
                vr = f["v"] / f["volume_avg"] if f.get("volume_avg") else 0
                ENT[k][t].append((s, M.stop(f, cfg), f.get("signal_score", vr), f.get("breaks_up", 0)))
    BARS[s] = b
    for k in V:
        SELL[k][s] = sl[k]
del raw
times = sorted(t for t in BARS["BTCUSDT"] if START <= t < END)
print(f"{TAG}: hazir {round(time.time()-t0)} sn | sinyal sayilari " + ", ".join(f"{k} {sum(len(x) for x in ENT[k].values())}" for k in V), flush=True)


def run(k, risk, s0=START, s1=END, trail=8.0, maxpos=10, quality=1.5):
    cash, pos, pend_buy, pend_sell, marks, curve, trades = 170.0, {}, [], set(), {}, [], []
    day, day_eq, halted = None, None, False
    def equity():
        return cash + sum(p["qty"] * marks.get(s, p["entry"]) for s, p in pos.items())
    def close(s, price, t):
        nonlocal cash
        p = pos.pop(s); px = price * (1 - SLIP)
        cash += p["qty"] * px * (1 - FEE)
        trades.append((s, (px - p["entry"]) / (p["entry"] - p["stop0"]), p["t_in"], t, p["qty"] * (px * (1 - FEE) - p["entry"] * (1 + FEE))))
    for t in times:
        if t // DAY != day:
            day, day_eq, halted = t // DAY, equity(), False
        for s in list(pend_sell):
            f = BARS.get(s, {}).get(t)
            if s in pos and f:
                close(s, f[0], t)
        pend_sell = set()
        eq = equity(); d = t // DAY * DAY
        allowed = daily.get(d, set()) if s0 <= d < s1 else set()
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
                if (mn / entry) * unit > eq * risk * quality:
                    continue
                qty = mn / entry
            # Futures margin, not cash: total open notional may reach LEV x equity (same as pit_sim2.run2)
            if sum(p["qty"] * marks.get(x, p["entry"]) for x, p in pos.items()) + qty * entry > LEV * eq:
                continue
            cash -= qty * entry * (1 + FEE)
            pos[s] = dict(qty=qty, entry=entry, stop=stop, stop0=stop, unit=unit, high=entry, t_in=t)
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
            if t in SELL[k].get(s, ()):
                pend_sell.add(s)
            p["high"] = max(p["high"], f[1])
            if p["high"] >= p["entry"] + p["unit"] and f[4]:
                p["stop"] = max(p["stop"], p["high"] - trail * f[4])
        if not halted:
            pend_buy = [x for x in ENT[k].get(t, []) if x[0] not in pos]
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
out = {}
print(f"{'':12} {'risk':>5} | {'TUM DONEM':>9} {'dusus':>6} {'islem/hf':>8} | 12 ay: {'medyan':>7} {'kotu%10':>7} {'en kotu':>7} {'en iyi':>7} {'zarar%':>6} {'dusus med/max':>13} {'batan':>5}", flush=True)
for k in V:
    for risk in (0.01,):
        c, tr = run(k, risk)
        weeks = (times[-1] - times[0]) / (7 * DAY)
        rets, dds = [], []
        for s0 in starts:
            cc, _ = run(k, risk, s0, s0 + YEAR)
            w = [(t, e) for t, e in cc if s0 <= t <= s0 + YEAR] or [(s0, 170.0)]
            rets.append(w[-1][1] / 170 - 1); dds.append(dd(w))
        sr = sorted(rets)
        out[f"{k} {risk}"] = dict(full=c[-1][1] / 170, full_dd=dd(c), rets=rets, dds=dds, trades=len(tr))
        print(f"{k:20} | {c[-1][1]/170:8.2f}x %{100*dd(c):4.0f} {len(tr)/weeks:8.1f} | "
              f"{100*S.median(rets):+6.0f}% {100*sr[len(sr)//10]:+6.0f}% {100*sr[0]:+6.0f}% {100*sr[-1]:+6.0f}% "
              f"%{100*sum(x<0 for x in rets)/len(rets):4.0f}   %{100*S.median(dds):3.0f}/%{100*max(dds):3.0f} "
              f"{sum(x <= -0.9 for x in rets):5d}", flush=True)
json.dump(out, open(Path(__file__).parent / f"pit_stopw_{TAG}.json", "w"))
print("BITTI", flush=True)
