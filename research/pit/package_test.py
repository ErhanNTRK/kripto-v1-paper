"""Final package test (29 Sep). Per era (PIT_DIR), per sleeve, 12-month windows starting every month:
  4H sleeve: (a) old rules, no gate  (b) live now: ETH/BTC>EMA50 gate
  2H sleeve: (1) live now (2 ATR)  (2) 3 ATR  (3) 3 ATR + dip  (4) 3 ATR + pyramid  (5) PACKAGE 3 ATR + pyramid + dip
             (6) 2 ATR + pyramid + dip
Whole system per window = weighted sum of the two sleeves' window returns (separate pots):
  yesterday 30/70 = 4H(a) + 2H(1) | today 35/65 = 4H(b) + 2H(1) | package 30/70 = 4H(b) + 2H(5)
Reports trades per week (entries, pyramid adds, dip fills) and what 100 USD becomes after 12 months
(median / worst / best / share of losing windows). Realistic costs, point-in-time top-50, data cut at symbol
re-use jumps, 4x aggregate margin cap, daily -10% halt."""
import os, sys, json, time, statistics as S
from collections import defaultdict
from pathlib import Path
from datetime import datetime, timezone
REPO = "C:/Users/ASUS-PC/Documents/GitHub/kripto-v1-paper"; sys.path.insert(0, REPO)
import crypto_v1.research_v5 as rv5
from crypto_v1.research_v5 import ShortWindowLongModel as M
from crypto_v1.github_worker import load_2h_strategy
from crypto_v1.risk import validate_config
PIT = Path(os.environ["PIT_DIR"]); TAG = "2021-23" if "pit2020" in str(PIT) else "2023-26"
DAY = 86_400_000
FEE, SLIP, LEV, DIP_COST = 0.0005, 0.0002, 4, 0.0007
MIN_NOTIONAL = {"ETHUSDT": 20.0, "BCHUSDT": 20.0, "LTCUSDT": 20.0, "BTCUSDT": 100.0}
os.chdir(REPO)
ORDERED = {int(k): [s for s in v if s != "BTCUSDT"] for k, v in json.loads((PIT / "daily_top50.json").read_text()).items()}
DAILY = {d: set(v) for d, v in ORDERED.items()}
EVER = sorted(set().union(*DAILY.values())); START = min(DAILY); END = max(DAILY) + DAY

def cut(rows):
    keep = rows[:1]
    for a, b in zip(rows, rows[1:]):
        if a["c"] > 0 and (b["o"] / a["c"] > 3 or b["o"] / a["c"] < 1 / 3 or b["t"] - a["t"] > 3 * DAY):
            break
        keep.append(b)
    return keep

def prepare(fl, cfg):
    t0 = time.time()
    raw = json.loads((PIT / f"data_{fl}.json").read_text(encoding="utf-8"))
    btc_rows = rv5.symmetric_features(raw.pop("BTCUSDT"), cfg)
    btc = {r["t"]: r for r in btc_rows}
    bars = {"BTCUSDT": {r["t"]: (r["o"], r["h"], r["l"], r["c"], r.get("atr")) for r in btc_rows if r["t"] >= START - 30 * DAY}}
    ent = {2.0: defaultdict(list), 3.0: defaultdict(list)}; sell = {}
    for s in list(raw):
        rows = cut(raw.pop(s))
        if len(rows) < 250 or (s not in EVER and s != "ETHUSDT"):
            continue
        b, sl = {}, set()
        for f in rv5.symmetric_features(rows, cfg):
            t = f["t"]
            if t < START - 30 * DAY:
                continue
            b[t] = (f["o"], f["h"], f["l"], f["c"], f.get("atr"))
            bt = btc.get(t)
            if bt is None or s not in EVER:
                continue
            if M.sell(f, bt, cfg):
                sl.add(t)
            if M.buy(f, bt, cfg):
                vr = f["v"] / f["volume_avg"] if f.get("volume_avg") else 0
                for m in (2.0, 3.0):
                    ent[m][t].append((s, f["c"] - m * f["atr"], f.get("signal_score", vr), f.get("breaks_up", 0)))
        bars[s], sell[s] = b, sl
    iv = min(b2 - b1 for b1, b2 in zip(sorted(bars["BTCUSDT"])[:2], sorted(bars["BTCUSDT"])[1:3]))
    # ETH/BTC > EMA50 on daily closes known the next day (last bar of each UTC day only)
    def dcl(sym):
        return {(t + iv) // DAY * DAY: v[3] for t, v in bars[sym].items() if (t + iv) % DAY == 0}
    e, bb = dcl("ETHUSDT"), dcl("BTCUSDT")
    gate, m = {}, None
    for d in sorted(set(e) & set(bb)):
        r = e[d] / bb[d]; m = r if m is None else m + (r - m) * 2 / 51; gate[d] = r > m
    times = sorted(t for t in bars["BTCUSDT"] if START <= t < END)
    print(f"{TAG} {fl}: hazir {round(time.time()-t0)} sn", flush=True)
    return dict(bars=bars, ent=ent, sell=sell, gate=gate, times=times, iv=iv)


def run(D, stop_mult=2.0, trail=8.0, maxpos=10, pyr=None, dip=False, gate=False, s0=START, s1=END,
        risk=0.01, quality=1.5, dip_n=10, dip_size=0.10, dip_depth=0.15, dip_tp=0.08, dip_stop=0.25):
    BARS, ENT, SELL, GATE, IV = D["bars"], D["ent"][stop_mult], D["sell"], D["gate"], D["iv"]
    cash, pos, dpos, pend_buy, pend_sell, pend_add, marks, curve = 170.0, {}, {}, [], set(), set(), {}, []
    cnt = {"giris": 0, "ekleme": 0, "igne": 0}
    day, day_eq, halted, orders = None, None, False, {}
    def notional():
        return (sum(p["qty"] * marks.get(s, p["entry0"]) for s, p in pos.items())
                + sum(p["qty"] * marks.get(s, p["entry"]) for s, p in dpos.items()))
    def equity():
        return cash + notional()
    def close(s, price):
        nonlocal cash
        p = pos.pop(s); cash += p["qty"] * price * (1 - SLIP) * (1 - FEE)
    def dclose(s, price):
        nonlocal cash
        p = dpos.pop(s); cash += p["qty"] * price * (1 - DIP_COST)
    for t in D["times"]:
        d = t // DAY * DAY
        if t // DAY != day:
            day, day_eq, halted, orders = t // DAY, equity(), False, {}
            if dip and s0 <= d < s1:
                for s in ORDERED.get(d, [])[:dip_n]:
                    prev = BARS.get(s, {}).get(d - IV)
                    if prev and s not in pos and s not in dpos:
                        orders[s] = prev[3] * (1 - dip_depth)
        for s in list(pend_sell):
            f = BARS.get(s, {}).get(t)
            if s in pos and f:
                close(s, f[0])
        pend_sell = set()
        eq = equity()
        for s in list(pend_add):
            p = pos.get(s); f = BARS.get(s, {}).get(t)
            if not p or not f or halted:
                continue
            px = f[0] * (1 + SLIP); q = p["q0"] * pyr["size"]
            if q * px < MIN_NOTIONAL.get(s, 5.0) * 1.03 or notional() + q * px > LEV * eq:
                continue
            cash -= q * px * (1 + FEE); p["qty"] += q; p["adds"] += 1; cnt["ekleme"] += 1
            if f[4]:
                p["stop"] = max(p["stop"], px - pyr["gap"] * f[4])
        pend_add = set()
        open_day = s0 <= d < s1 and (not gate or GATE.get(d, False))
        allowed = DAILY.get(d, set()) if open_day else set()
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
            cash -= qty * entry * (1 + FEE); cnt["giris"] += 1
            pos[s] = dict(qty=qty, q0=qty, entry0=entry, stop=stop, unit=unit, high=entry, adds=0)
            orders.pop(s, None)
        pend_buy = []
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
            px = min(limit, f[0]) * (1 + DIP_COST); qty = dip_size * eq / px
            if qty * px < MIN_NOTIONAL.get(s, 5.0) * 1.03 or notional() + qty * px > LEV * eq:
                continue
            cash -= qty * px; cnt["igne"] += 1
            dpos[s] = dict(qty=qty, entry=px, tp=px * (1 + dip_tp), stop=px * (1 - dip_stop), until=t + DAY)
            marks[s] = f[3]
            if f[2] <= px * (1 - dip_stop):
                dclose(s, px * (1 - dip_stop))
        for s, p in list(pos.items()):
            f = BARS.get(s, {}).get(t)
            if f and f[2] <= p["stop"]:
                close(s, min(f[0], p["stop"]))
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
        if not halted:
            pend_buy = [x for x in ENT.get(t, []) if x[0] not in pos]
        e = equity()
        if e <= day_eq * 0.9 and not halted:
            halted = True; pend_sell |= set(pos)
            for s in list(dpos):
                f = BARS.get(s, {}).get(t)
                if f:
                    dclose(s, f[3])
            orders = {}
        curve.append((t + D["iv"], e))
        if e <= 1.0:
            break
    return curve, cnt


PYR = dict(levels=(2, 4), size=1.0, gap=4.0)
ts = lambda y, m: int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)
STARTS = ([ts(2021 + (m - 1) // 12, (m - 1) % 12 + 1) for m in range(1, 22)] if TAG == "2021-23"
          else [ts(2023 + (m - 1) // 12, (m - 1) % 12 + 1) for m in range(9, 33)])
YEAR = 365 * DAY
WEEKS = 52.14

def windows(D, **kw):
    rets, cnts = [], []
    for s0 in STARTS:
        c, cnt = run(D, s0=s0, s1=s0 + YEAR, **kw)
        w = [(t, e) for t, e in c if s0 <= t <= s0 + YEAR] or [(s0, 170.0)]
        rets.append(w[-1][1] / 170 - 1); cnts.append(cnt)
    per_week = {k: S.mean(c[k] for c in cnts) / WEEKS for k in ("giris", "ekleme", "igne")}
    return rets, per_week

def line(name, rets, pw):
    sr = sorted(rets)
    total = pw["giris"] + pw["ekleme"] + pw["igne"]
    return (f"{name:44} | haftada {total:4.1f} islem (giris {pw['giris']:.1f} ekleme {pw['ekleme']:.1f} igne {pw['igne']:.1f}) | "
            f"100$ 12 ay sonra: tipik {100*(1+S.median(rets)):6.0f}$  en kotu {100*(1+sr[0]):5.0f}$  en iyi {100*(1+sr[-1]):6.0f}$ | "
            f"zararli yil %{100*sum(x<0 for x in rets)/len(rets):3.0f}")

out = {}
C4 = validate_config(json.loads(Path("config_v5_long.json").read_text(encoding="utf-8")))
D4 = prepare("4h", dict(C4, entry_mode="breakout"))
for name, kw in (("4H eski (filtresiz)", dict(trail=6.0, maxpos=6)),
                 ("4H bugun (ETH/BTC filtreli)", dict(trail=6.0, maxpos=6, gate=True))):
    out[name] = windows(D4, **kw); print(line(name, *out[name]), flush=True)
del D4
D2 = prepare("2h", dict(load_2h_strategy(relax=False), entry_mode="breakout"))
for name, kw in (("2H bugun (2 ATR)", dict()),
                 ("2H 3 ATR", dict(stop_mult=3.0)),
                 ("2H 3 ATR + igne", dict(stop_mult=3.0, dip=True)),
                 ("2H 3 ATR + piramit", dict(stop_mult=3.0, pyr=PYR)),
                 ("2H PAKET: 3 ATR + piramit + igne", dict(stop_mult=3.0, pyr=PYR, dip=True)),
                 ("2H 2 ATR + piramit + igne", dict(pyr=PYR, dip=True))):
    out[name] = windows(D2, **kw); print(line(name, *out[name]), flush=True)

def combo(a, wa, b, wb):
    rets = [wa * x + wb * y for x, y in zip(out[a][0], out[b][0])]
    pw = {k: out[a][1][k] + out[b][1][k] for k in out[a][1]}
    return rets, pw
print(f"\n=== {TAG}: TUM SISTEM (100$ iki kasaya bolunmus) ===")
for name, args in (("DUNKU sistem 30/70", ("4H eski (filtresiz)", 0.30, "2H bugun (2 ATR)", 0.70)),
                   ("BUGUNKU canli sistem 35/65", ("4H bugun (ETH/BTC filtreli)", 0.35, "2H bugun (2 ATR)", 0.65)),
                   ("PAKET 30/70", ("4H bugun (ETH/BTC filtreli)", 0.30, "2H PAKET: 3 ATR + piramit + igne", 0.70)),
                   ("PAKET, piramitsiz 30/70", ("4H bugun (ETH/BTC filtreli)", 0.30, "2H 3 ATR + igne", 0.70))):
    print(line(name, *combo(*args)), flush=True)
json.dump({k: v[0] for k, v in out.items()}, open(Path(__file__).parent / f"package_{TAG}.json", "w"))
print("BITTI", flush=True)
