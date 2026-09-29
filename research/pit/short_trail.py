"""4H shorts (live: only while BTC < 200-day MA) with the live close-based trailing rule, which
research_v5.run_symmetric never modelled: once the low reaches entry - 1R, exit on a close >= low + trail*ATR.
Question: does widening the trail 4 -> 6 ATR (side effect of 111c1de) hurt the shorts? Cached data only."""
import json, os, sys, datetime
from pathlib import Path
REPO = "C:/Users/ASUS-PC/Documents/GitHub/kripto-v1-paper"; os.chdir(REPO); sys.path.insert(0, REPO)
import crypto_v1.research_v5 as rv5
from crypto_v1.research_v2 import FOUR_HOUR
from crypto_v1.risk import validate_config

CACHE = Path("C:/Users/ASUS-PC/kripto/arastirma-verisi")
meta = json.loads((CACHE / "meta.json").read_text(encoding="utf-8"))
symbols, start, end = meta["symbols"], meta["test_start"], meta["end"]
DAY = 86_400_000
cfg = dict(validate_config(json.loads(Path("config_v5_long.json").read_text(encoding="utf-8"))),
           max_positions=6, risk_fraction=0.01, initial_cash=50.0, short_regime_only=True)


def run(prepared, times, trail):
    fee, slip = cfg["fee"], cfg["slippage"]
    cash, pos, pending, trades, curve, marks = cfg["initial_cash"], {}, {}, [], [], {}
    eq = lambda: cash - sum(p["qty"] * marks.get(s, p["entry"]) for s, p in pos.items())

    def close(s, price, t, why):
        nonlocal cash
        p = pos.pop(s)
        fill = price * (1 + slip)
        cost = p["qty"] * fill * (1 + fee)
        pnl = p["qty"] * p["entry"] * (1 - fee) - cost
        cash -= cost
        trades.append(dict(pnl=pnl, r=pnl / (p["unit"] * p["qty"]), why=why, t=t))

    for t in times:
        bars = {s: prepared[s][t] for s in prepared if t in prepared[s]}
        marks.update({s: f["o"] for s, f in bars.items()})
        for s, stop in list(pending.items()):
            if s in pos or s not in bars or len(pos) >= cfg["max_positions"]:
                continue
            entry = bars[s]["o"] * (1 - slip)
            unit = stop - entry
            if unit <= 0:
                continue
            qty = min(eq() * cfg["risk_fraction"] / unit, cash * 0.9 / entry)
            if qty * entry >= 5:
                cash += qty * entry * (1 - fee)
                pos[s] = dict(qty=qty, entry=entry, stop=stop, unit=unit, low=entry)
        pending = {}
        for s, p in list(pos.items()):
            f = bars.get(s)
            if f and f["h"] >= p["stop"]:
                close(s, max(f["o"], p["stop"]), t, "stop")
        for s, p in list(pos.items()):
            f = bars.get(s)
            if not f:
                continue
            p["low"] = min(p["low"], f["l"])
            if rv5.short_exit(f, bars.get("BTCUSDT"), cfg.get("btc_filter", "strict")):
                close(s, f["c"], t, "trend"); continue
            if trail and f.get("atr") and p["low"] <= p["entry"] - p["unit"] and f["c"] >= p["low"] + trail * f["atr"]:
                close(s, f["c"], t, "trail")
        marks.update({s: f["c"] for s, f in bars.items()})
        for s in symbols:
            f = bars.get(s)
            if not f or s in pos:
                continue
            st = rv5.short_entry(f, bars.get("BTCUSDT"), cfg)
            if st is not None:
                pending[s] = st
        curve.append((t + FOUR_HOUR, eq()))
    return trades, curve


def report(name, trades, curve):
    mk = lambda ms: datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc)
    half, prev, halves = {}, 50.0, []
    for tt, e in curve:
        d = mk(tt); half[(d.year, (d.month - 1) // 6)] = e
    for k in sorted(half):
        halves.append(f"{100*(half[k]/prev-1):+.0f}%"); prev = half[k]
    peak, dd = 50.0, 0.0
    for _, e in curve:
        peak = max(peak, e); dd = max(dd, 1 - e / peak)
    w = sum(x["pnl"] for x in trades if x["pnl"] > 0); l = -sum(x["pnl"] for x in trades if x["pnl"] < 0)
    n = len(trades)
    print(f"{name:22} islem {n:4d} | ort {sum(x['r'] for x in trades)/max(n,1):+.2f}R | PF {w/l if l else 0:.2f} | "
          f"50 -> {curve[-1][1]:6.1f} | dusus %{100*dd:.0f} | iz ile cikan {sum(x['why']=='trail' for x in trades)} | 6 ay {' '.join(halves)}",
          flush=True)


data = json.loads((CACHE / "data_4h.json").read_text(encoding="utf-8"))
prepared = {s: {r["t"]: r for r in rv5.symmetric_features(rows, cfg)} for s, rows in data.items()}
times = sorted(t for t in prepared["BTCUSDT"] if start <= t < end)
print(f"4H short, BTC 200g altinda, {len(symbols)} coin, {mk if False else ''}{len(times)} bar")
for name, tr in (("iz yok (eski testler)", None), ("iz 3 ATR", 3.0), ("iz 4 ATR (eski canli)", 4.0),
                 ("iz 5 ATR", 5.0), ("iz 6 ATR (YENI canli)", 6.0), ("iz 8 ATR", 8.0)):
    report(name, *run(prepared, times, tr))
print("BITTI", flush=True)
