"""Three fixes after the live audit (30 Sep 2026): the bot's own closes won 4 of 32, and half of its trades had
been +2% up before they turned into losses. Per era (PIT_DIR), per sleeve, 12-month windows starting every month:
  min stop 3%   the stop is at least 3% under the entry (TRX: an ATR stop 1% away made a 235 USDT position)
  pump filter   no entry when the signal bar's close is more than 20% above the lowest low of the last 24 hours
                (QNT: +25% in a day with a wick to a new high)
  take profit   sell the whole position at +2% / +3% (intrabar, the stop checked first), or move the stop to
                break-even once the price has been +2% up
Reports trades per week, the share of trades closed in profit, and what 100 USD becomes after 12 months.
Same engine as package_test.py (realistic costs, point-in-time top-50, data cut at symbol re-use jumps,
4x aggregate margin cap, daily -10% halt), without the dip-catcher and the pyramid."""
import os, sys, json, time, statistics as S
from collections import defaultdict, deque
from pathlib import Path
from datetime import datetime, timezone
REPO = "C:/Users/ASUS-PC/Documents/GitHub/kripto-v1-paper"; sys.path.insert(0, REPO)
import crypto_v1.research_v5 as rv5
from crypto_v1.research_v5 import ShortWindowLongModel as M
from crypto_v1.github_worker import load_2h_strategy
from crypto_v1.risk import validate_config
PIT = Path(os.environ["PIT_DIR"]); TAG = "2021-23" if "pit2020" in str(PIT) else "2023-26"
DAY = 86_400_000
FEE, SLIP, LEV = 0.0005, 0.0002, 4
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


def prepare(fl, cfg, stop_mult):
    t0 = time.time()
    raw = json.loads((PIT / f"data_{fl}.json").read_text(encoding="utf-8"))
    btc_rows = rv5.symmetric_features(raw.pop("BTCUSDT"), cfg)
    btc = {r["t"]: r for r in btc_rows}
    bars = {"BTCUSDT": {r["t"]: (r["o"], r["h"], r["l"], r["c"], r.get("atr")) for r in btc_rows if r["t"] >= START - 30 * DAY}}
    n24 = 12 if fl == "2h" else 6
    ent, sell = defaultdict(list), {}
    for s in list(raw):
        rows = cut(raw.pop(s))
        if len(rows) < 250 or (s not in EVER and s != "ETHUSDT"):
            continue
        b, sl, lows = {}, set(), deque(maxlen=n24)
        for f in rv5.symmetric_features(rows, cfg):
            t = f["t"]; lows.append(f["l"])
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
                pump = f["c"] / min(lows) - 1 if min(lows) > 0 else 0
                ent[t].append((s, f["c"] - stop_mult * f["atr"], f.get("signal_score", vr), f.get("breaks_up", 0), pump))
        bars[s], sell[s] = b, sl
    iv = min(b2 - b1 for b1, b2 in zip(sorted(bars["BTCUSDT"])[:2], sorted(bars["BTCUSDT"])[1:3]))
    def dcl(sym):
        return {(t + iv) // DAY * DAY: v[3] for t, v in bars[sym].items() if (t + iv) % DAY == 0}
    e, bb = dcl("ETHUSDT"), dcl("BTCUSDT")
    gate, m = {}, None
    for d in sorted(set(e) & set(bb)):
        r = e[d] / bb[d]; m = r if m is None else m + (r - m) * 2 / 51; gate[d] = r > m
    times = sorted(t for t in bars["BTCUSDT"] if START <= t < END)
    print(f"{TAG} {fl}: hazir {round(time.time()-t0)} sn", flush=True)
    return dict(bars=bars, ent=ent, sell=sell, gate=gate, times=times, iv=iv)


def run(D, trail=8.0, maxpos=10, gate=False, s0=START, s1=END, risk=0.01, quality=1.5,
        min_stop=None, pump=None, tp=None, be=None):
    BARS, ENT, SELL, GATE = D["bars"], D["ent"], D["sell"], D["gate"]
    cash, pos, pend_buy, pend_sell, marks, curve = 170.0, {}, [], set(), {}, []
    cnt = {"giris": 0, "kazanan": 0, "kapanan": 0}
    day, day_eq, halted = None, None, False
    def notional():
        return sum(p["qty"] * marks.get(s, p["entry"]) for s, p in pos.items())
    def equity():
        return cash + notional()
    def close(s, price):
        nonlocal cash
        p = pos.pop(s); back = p["qty"] * price * (1 - SLIP) * (1 - FEE); cash += back
        cnt["kapanan"] += 1; cnt["kazanan"] += back > p["cost"]
    for t in D["times"]:
        d = t // DAY * DAY
        if t // DAY != day:
            day, day_eq, halted = t // DAY, equity(), False
        for s in list(pend_sell):
            f = BARS.get(s, {}).get(t)
            if s in pos and f:
                close(s, f[0])
        pend_sell = set()
        eq = equity()
        open_day = s0 <= d < s1 and (not gate or GATE.get(d, False))
        allowed = DAILY.get(d, set()) if open_day else set()
        for s, stop, score, breaks, pmp in sorted(pend_buy, key=lambda x: (-x[2], x[0])):
            if halted or len(pos) >= maxpos:
                break
            f = BARS.get(s, {}).get(t)
            if not f or s in pos or s not in allowed:
                continue
            if pump is not None and pmp > pump:
                continue
            entry = f[0] * (1 + SLIP)
            if min_stop is not None:
                stop = min(stop, entry * (1 - min_stop))
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
            pos[s] = dict(qty=qty, entry=entry, stop=stop, unit=unit, high=entry, cost=qty * entry * (1 + FEE),
                          tp=entry * (1 + tp) if tp else None)
        pend_buy = []
        for s, p in list(pos.items()):
            f = BARS.get(s, {}).get(t)
            if not f:
                continue
            if f[2] <= p["stop"]:
                close(s, min(f[0], p["stop"]))
            elif p["tp"] and f[1] >= p["tp"]:
                close(s, max(f[0], p["tp"]))
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
            if be and p["high"] >= p["entry"] * (1 + be):
                p["stop"] = max(p["stop"], p["entry"] * (1 + 2 * FEE + 2 * SLIP))
        if not halted:
            pend_buy = [x for x in ENT.get(t, []) if x[0] not in pos]
        e = equity()
        if e <= day_eq * 0.9 and not halted:
            halted = True; pend_sell |= set(pos)
        curve.append((t + D["iv"], e))
        if e <= 1.0:
            break
    return curve, cnt


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
    stats = {"hafta": S.mean(c["giris"] for c in cnts) / WEEKS,
             "kazanan": sum(c["kazanan"] for c in cnts) / max(1, sum(c["kapanan"] for c in cnts))}
    return rets, stats


def line(name, rets, st):
    sr = sorted(rets)
    return (f"{name:40} | haftada {st['hafta']:4.1f} alim | karla kapanan %{100*st['kazanan']:3.0f} | "
            f"100$ 12 ay sonra: tipik {100*(1+S.median(rets)):5.0f}$  en kotu {100*(1+sr[0]):4.0f}$  "
            f"en iyi {100*(1+sr[-1]):5.0f}$ | zararli yil %{100*sum(x<0 for x in rets)/len(rets):3.0f}")


VARIANTS = (("simdiki kurallar", {}),
            ("1) en az %3 stop", dict(min_stop=0.03)),
            ("2) pompa filtresi (%20)", dict(pump=0.20)),
            ("3a) kar al +%2", dict(tp=0.02)),
            ("3b) kar al +%3", dict(tp=0.03)),
            ("3c) +%2'de stop girise", dict(be=0.02)),
            ("1+2+3b hepsi (kar al +%3)", dict(min_stop=0.03, pump=0.20, tp=0.03)),
            ("1+2+3c hepsi (stop girise)", dict(min_stop=0.03, pump=0.20, be=0.02)))

out = {}
C4 = validate_config(json.loads(Path("config_v5_long.json").read_text(encoding="utf-8")))
D4 = prepare("4h", dict(C4, entry_mode="breakout"), 2.0)
for name, kw in VARIANTS:
    key = "4H " + name
    out[key] = windows(D4, trail=6.0, maxpos=6, gate=True, **kw); print(line(key, *out[key]), flush=True)
del D4
D2 = prepare("2h", dict(load_2h_strategy(relax=False), entry_mode="breakout"), 3.0)
for name, kw in VARIANTS:
    key = "2H " + name
    out[key] = windows(D2, **kw); print(line(key, *out[key]), flush=True)

print(f"\n=== {TAG}: TUM SISTEM 25/75 (ayni kural iki kasada) ===")
for name, _ in VARIANTS:
    a, b = out["4H " + name], out["2H " + name]
    rets = [0.25 * x + 0.75 * y for x, y in zip(a[0], b[0])]
    st = {"hafta": a[1]["hafta"] + b[1]["hafta"],
          "kazanan": (a[1]["kazanan"] * a[1]["hafta"] + b[1]["kazanan"] * b[1]["hafta"]) / max(1e-9, a[1]["hafta"] + b[1]["hafta"])}
    print(line(name, rets, st), flush=True)
json.dump({k: v[0] for k, v in out.items()}, open(Path(__file__).parent / f"rule_fix_{TAG}.json", "w"))
print("BITTI", flush=True)
