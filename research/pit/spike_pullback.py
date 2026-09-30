"""The user's rule (30 Sep 2026): a big green candle that makes the highest level, then a red candle; buy after
the red candle closes (AVAX 29 Sep: big candle to 12.0, big red at 17:00, buy ~11.2 at 19:00).
Event study on the point-in-time top-50, 2H and 4H bars, both eras (PIT_DIR), realistic costs, data cut at
symbol re-use jumps. Variants:
  big  : green body >= 2 ATR or >= 3 ATR, and its high is the highest high of the last 50 bars
  red  : the NEXT bar is red (any), or red with body >= 1 ATR
  entry: open of the bar after the red candle
  exits: (T) target = big candle high, stop = red candle low - 0.25 ATR, max 24h
         (R) stop 2 ATR, target +2R, max 24h
         (H) hold 24 hours, stop 2 ATR
  filter: none, or BTC > EMA50 on that timeframe and coin Ichimoku fully bullish at the red candle
Output per variant: trades, win rate, average R, PF(R), total R, and a random-entry baseline (same coins/bars)."""
import os, sys, json, random, statistics as S
from datetime import datetime, timezone
from pathlib import Path
REPO = "C:/Users/ASUS-PC/Documents/GitHub/kripto-v1-paper"; sys.path.insert(0, REPO)
import crypto_v1.research_v5 as rv5
from crypto_v1.github_worker import load_2h_strategy
from crypto_v1.risk import validate_config
PIT = Path(os.environ["PIT_DIR"]); TAG = "2021-23" if "pit2020" in str(PIT) else "2023-26"
DAY = 86_400_000
FEE, SLIP = 0.0005, 0.0002
os.chdir(REPO)
daily = {int(k): set(v) for k, v in json.loads((PIT / "daily_top50.json").read_text()).items()}
ever = set().union(*daily.values())

def cut(rows):
    keep = rows[:1]
    for a, b in zip(rows, rows[1:]):
        if a["c"] > 0 and (b["o"] / a["c"] > 3 or b["o"] / a["c"] < 1 / 3 or b["t"] - a["t"] > 3 * DAY):
            break
        keep.append(b)
    return keep

def trade(rows, i, stop, target, max_bars):
    """Enter at rows[i] open; walk forward; stop checked before target (conservative). R from entry-stop."""
    entry = rows[i]["o"] * (1 + SLIP) * (1 + FEE)
    risk = entry - stop
    if risk <= 0:
        return None
    for j in range(i, min(len(rows), i + max_bars)):
        r = rows[j]
        if r["l"] <= stop:
            out = min(r["o"], stop) if j > i else stop
            return (out * (1 - SLIP) * (1 - FEE) - entry) / risk
        if target and r["h"] >= target and not (j == i and r["o"] < target and False):
            out = max(r["o"], target)
            return (out * (1 - SLIP) * (1 - FEE) - entry) / risk
    last = rows[min(len(rows), i + max_bars) - 1]["c"]
    return (last * (1 - SLIP) * (1 - FEE) - entry) / risk

def stats(rs):
    if not rs:
        return "  yok"
    w = sum(x for x in rs if x > 0); l = -sum(x for x in rs if x <= 0)
    return (f"n {len(rs):5d} | kazanan %{100*sum(x>0 for x in rs)/len(rs):3.0f} | ort {S.mean(rs):+.2f}R | "
            f"PF {w/l if l else 0:4.2f} | toplam {sum(rs):+7.0f}R")

for fl, cfg, bars24 in (("2h", dict(load_2h_strategy(relax=False), entry_mode="breakout+momentum"), 12),
                        ("4h", dict(validate_config(json.loads(Path("config_v5_long.json").read_text(encoding="utf-8"))),
                                    entry_mode="breakout+momentum"), 6)):
    raw = json.loads((PIT / f"data_{fl}.json").read_text(encoding="utf-8"))
    btc = {r["t"]: r for r in rv5.symmetric_features(raw.pop("BTCUSDT"), cfg)}
    res, base = {}, []
    rnd = random.Random(7)
    for s in list(raw):
        rows = cut(raw.pop(s))
        if s not in ever or len(rows) < 120:
            continue
        feats = rv5.symmetric_features(rows, cfg)
        for i in range(51, len(feats) - 2):
            big, red = feats[i - 1], feats[i]           # i-1 = big candle, i = red candle, entry at i+1 open
            d = feats[i + 1]["t"] // DAY * DAY
            if s not in daily.get(d, ()) or not big.get("atr"):
                continue
            atr = big["atr"]
            if rnd.random() < 0.02:                     # random-entry baseline, 2H/4H alike
                base.append(trade(feats, i + 1, feats[i + 1]["o"] - 2 * atr, None, bars24))
            body = big["c"] - big["o"]
            if body < 2 * atr or big["h"] < max(f["h"] for f in feats[i - 51:i - 1]):
                continue
            if red["c"] >= red["o"]:
                continue
            bt = btc.get(red["t"])
            filt = bool(bt and bt.get("ema50") and bt["c"] > bt["ema50"] and red.get("ichimoku_ok") is True)
            entry_open = feats[i + 1]["o"]
            for bigname, need in (("buyuk>=2ATR", 2), ("buyuk>=3ATR", 3)):
                if body < need * atr:
                    continue
                for redname, redneed in (("herhangi kirmizi", 0), ("kirmizi>=1ATR", 1)):
                    if red["o"] - red["c"] < redneed * atr:
                        continue
                    for exname, stop, target in (("T: zirveye don / kirmizi dip stop", red["l"] - 0.25 * atr, big["h"]),
                                                 ("R: 2ATR stop / +2R", entry_open - 2 * atr, entry_open + 4 * atr),
                                                 ("H: 24 saat / 2ATR stop", entry_open - 2 * atr, None)):
                        r = trade(feats, i + 1, stop, target, bars24)
                        if r is None:
                            continue
                        for fname, ok in (("filtresiz", True), ("BTC+Ichimoku", filt)):
                            if ok:
                                res.setdefault((bigname, redname, exname, fname), []).append(r)
    print(f"\n=== {TAG} {fl.upper()} | rastgele giris (2ATR stop, 24s): {stats([x for x in base if x is not None])}", flush=True)
    for k in sorted(res):
        print(f"  {k[0]:11} {k[1]:16} {k[2]:34} {k[3]:13} {stats(res[k])}", flush=True)
    del raw
print("BITTI")
