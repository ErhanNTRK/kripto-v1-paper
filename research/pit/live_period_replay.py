"""Is the test method wrong? (user, 2 Oct 2026). Replays the 2H system with the same engine as the research tests
over exactly the live period (entries 22 Sep 18:00 UTC - 30 Sep 12:00 UTC 2026), on fresh Binance futures 2H bars,
and lists every simulated trade next to what the live bot actually did (islemler.csv).
  A "rules then": breakout + momentum entries, 2-ATR stop, 6-ATR trail, 0.75% risk, no ETH/BTC gate, no pump
    filter, no break-even (roughly the live configuration for most of 22-29 Sep)
  B "rules now": breakout only, 3-ATR stop, 8-ATR trail, 1% risk, 3% min stop, pump filter, break-even at +2%
Positions are followed until now (no hand closes), the daily -10% halt applies."""
import csv, json, os, sys, time, urllib.request
from collections import defaultdict, deque
from pathlib import Path

REPO = "C:/Users/ASUS-PC/Documents/GitHub/kripto-v1-paper"; sys.path.insert(0, REPO); os.chdir(REPO)
import crypto_v1.research_v5 as rv5
from crypto_v1.research_v5 import ShortWindowLongModel as M
from crypto_v1.github_worker import load_2h_strategy

H2, DAY = 7_200_000, 86_400_000
FEE, SLIP, LEV = 0.0005, 0.0002, 4
E0, E1 = 1_790_100_000_000, 1_790_769_600_000          # 22 Sep 2026 18:00 UTC .. 30 Sep 12:00 UTC
E0 = 1_790_100_000_000 - (1_790_100_000_000 % H2)


def klines(sym, start):
    out = []
    while True:
        k = json.load(urllib.request.urlopen(
            f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=2h&startTime={start}&limit=1500"))
        out += k
        if len(k) < 1500: break
        start = int(k[-1][0]) + H2
    now = int(time.time() * 1000)
    return [{"t": int(r[0]), "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4]), "v": float(r[5])}
            for r in out if int(r[6]) < now]


def build(coins, rows_of, btc_rows_raw, cfg, stop_mult):
    btc_rows = rv5.symmetric_features(btc_rows_raw, cfg); btc = {r["t"]: r for r in btc_rows}
    bars, ent, sell = {}, defaultdict(list), {}
    for s in coins:
        rows = rows_of.get(s)
        if not rows or len(rows) < 300: continue
        b, sl, lows = {}, set(), deque(maxlen=12)
        for f in rv5.symmetric_features(rows, cfg):
            t = f["t"]; lows.append(f["l"])
            b[t] = (f["o"], f["h"], f["l"], f["c"], f.get("atr"))
            bt = btc.get(t)
            if bt is None: continue
            if M.sell(f, bt, cfg): sl.add(t)
            if M.buy(f, bt, cfg):
                vr = f["v"] / f["volume_avg"] if f.get("volume_avg") else 0
                ent[t].append((s, f["c"] - stop_mult * f["atr"], f.get("signal_score", vr), f.get("breaks_up", 0),
                               f["c"] / min(lows) - 1 if min(lows) > 0 else 0))
        bars[s], sell[s] = b, sl
    return bars, ent, sell


def run(bars, ent, sell, times, trail, risk, min_stop=None, pump=None, be=None, maxpos=10):
    cash, pos, pend_buy, pend_sell, marks, log = 119.0, {}, [], set(), {}, []
    day, day_eq, halted = None, None, False
    def notional(): return sum(p["qty"] * marks.get(s, p["entry"]) for s, p in pos.items())
    def equity(): return cash + notional()
    def close(s, price, t, why):
        nonlocal cash
        p = pos.pop(s); back = p["qty"] * price * (1 - SLIP) * (1 - FEE); cash += back
        log.append((p["t_in"], s, p["entry"], price, why, back - p["cost"], t))
    for t in times:
        if t < E0: continue
        if t // DAY != day: day, day_eq, halted = t // DAY, equity(), False
        for s in list(pend_sell):
            f = bars.get(s, {}).get(t)
            if s in pos and f: close(s, f[0], t, "cikis kurali")
        pend_sell = set(); eq = equity()
        for s, stop, score, breaks, pmp in sorted(pend_buy, key=lambda x: (-x[2], x[0])):
            if halted or len(pos) >= maxpos or t > E1: break
            f = bars.get(s, {}).get(t)
            if not f or s in pos or (pump and pmp > pump): continue
            entry = f[0] * (1 + SLIP)
            if min_stop: stop = min(stop, entry * (1 - min_stop))
            if stop <= 0 or entry <= stop: continue
            unit = entry * (1 + FEE) - stop * (1 - SLIP) * (1 - FEE)
            r = risk * (1.5 if breaks >= 1 else 0.5)
            qty = min(eq * r / unit, LEV * eq / (entry * (1 + FEE)))
            if qty * entry < 5.15:
                if (5.15 / entry) * unit > eq * r: continue
                qty = 5.15 / entry
            if notional() + qty * entry > LEV * eq: continue
            cash -= qty * entry * (1 + FEE)
            pos[s] = dict(qty=qty, entry=entry, stop=stop, unit=unit, high=entry, cost=qty * entry * (1 + FEE), t_in=t)
        pend_buy = []
        for s, p in list(pos.items()):
            f = bars.get(s, {}).get(t)
            if f and f[2] <= p["stop"]: close(s, min(f[0], p["stop"]), t, "stop")
        for s, p in pos.items():
            f = bars.get(s, {}).get(t)
            if not f: continue
            marks[s] = f[3]
            if t in sell.get(s, ()): pend_sell.add(s)
            p["high"] = max(p["high"], f[1])
            if p["high"] >= p["entry"] + p["unit"] and f[4]: p["stop"] = max(p["stop"], p["high"] - trail * f[4])
            if be and p["high"] >= p["entry"] * (1 + be): p["stop"] = max(p["stop"], p["entry"] * (1 + 2 * FEE + 2 * SLIP))
        if not halted and t <= E1: pend_buy = [x for x in ent.get(t, []) if x[0] not in pos]
        if equity() <= day_eq * 0.9 and not halted: halted = True; pend_sell |= set(pos)
    for s, p in list(pos.items()):
        log.append((p["t_in"], s, p["entry"], marks.get(s, p["entry"]), "hala acik", p["qty"] * (marks.get(s, p["entry"]) - p["entry"]), None))
    return log


if __name__ == "__main__":
    f = lambda t: time.strftime("%d.%m %H:%M", time.localtime(t / 1000))
    live = [r for r in csv.DictReader(open(Path.home() / "kripto/islem-kayitlari/islemler.csv", encoding="utf-8-sig"), delimiter=";")
            if r["sistem"] == "2H"]
    manifest = json.loads((Path.home() / "kripto/src/runtime/manifest.json").read_text(encoding="utf-8"))["symbols"]
    coins = sorted(set(s for s in manifest if s != "BTCUSDT") | {r["coin"] for r in live} | {"ETHUSDT"})
    t0 = time.time()
    btc_raw = klines("BTCUSDT", 1_767_225_600_000)                   # from 1 Jan 2026 for the 200-day regime
    rows_of = {}
    for s in coins:
        try: rows_of[s] = klines(s, 1_782_864_000_000)               # from 1 Jul 2026
        except Exception as e: print("  veri yok:", s, e)
    print(f"{len(rows_of)} coin indirildi ({round(time.time()-t0)} sn)\n")
    base = load_2h_strategy(relax=False)
    times = sorted(r["t"] for r in btc_raw)
    for name, cfg, sm, kw in (
            ("A) O GUNLERIN KURALLARI (kirilim+momentum, 2 ATR stop, iz 6)", dict(base, entry_mode="breakout+momentum"), 2.0,
             dict(trail=6.0, risk=0.0075)),
            ("B) BUGUNKU KURALLAR (kirilim, 3 ATR, iz 8, %3 min stop, pompa, girise stop)", dict(base, entry_mode="breakout"), 3.0,
             dict(trail=8.0, risk=0.01, min_stop=0.03, pump=0.20, be=0.02))):
        bars, ent, sell = build(list(rows_of), rows_of, btc_raw, cfg, sm)
        log = run(bars, ent, sell, times, **kw)
        closed = [x for x in log if x[4] != "hala acik"]
        print(f"=== {name}: {len(log)} islem, kazanan {sum(x[5] > 0 for x in closed)}/{len(closed)} kapanan, "
              f"toplam {sum(x[5] for x in log):+.2f} USDT (119 USDT kasa) ===")
        for t_in, s, e, x, why, pnl, t_out in sorted(log):
            print(f"  {f(t_in + H2)} {s:14} giris {e:.6g} cikis {x:.6g} ({100*(x/e-1):+5.1f}%) {why:12} {pnl:+.2f}")
        print()
    print("=== CANLI BOT (2H, ayni donem) ===")
    for r in sorted(live, key=lambda r: r["acilis"]):
        print(f"  {r['acilis'][8:10]}.{r['acilis'][5:7]} {r['acilis'][11:]} {r['coin']:14} giris {float(r['giris_fiyati']):.6g} "
              f"cikis {float(r['cikis_fiyati']):.6g} ({100*(float(r['cikis_fiyati'])/float(r['giris_fiyati'])-1):+5.1f}%) {r['kapanis_turu']:12} {float(r['net_kz_usdt']):+.2f}")
