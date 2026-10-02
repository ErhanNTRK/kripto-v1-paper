"""Paper homework (user, 2 Oct 2026): pick 8 top-100 USDT-M coins with the bot's own 2H rules at high risk,
then follow them on real prices. Two paper portfolios of 224 USDT each (A filtered, B unfiltered). PAPER ONLY: public market data, no keys, no orders.

  python tools/odev.py sec            pick 8 coins now and open the paper positions
  python tools/odev.py izle           check stops/targets on 5m bars and print the report
  python tools/odev.py iptal ONDO     the user's veto: note the price now; the coin is still followed
  python tools/odev.py tahmin "..."   note the user's own market call with BTC's price now
  python tools/odev.py dongu [dk]     izle every N minutes (default 15)

Method (the bot's 2H trend rules, ranked for 8 slots):
  - universe: top 100 USDT-M perpetuals by 24h quote volume, stablecoins excluded;
  - a coin's direction: LONG when close > EMA20 > EMA50, SHORT when close < EMA20 < EMA50, else skipped;
  - score: Donchian 20/40/80 breaks in that direction (0-3), +3 when the bot's own long_entry fires,
    + strength vs BTC over 7 days; coins up/down more than 20% in 24h are skipped (pump filter);
  - stop 5 ATR (2H) from entry, at least 3%; stop to entry (+fees) once +2%; target 2R (10 ATR);
  - size: 8 equal margins, 96% of the capital; leverage so a stop costs ~75% of that margin (1x..20x).
"""
import json, sys, time, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from crypto_v1.research_v5 import symmetric_features, long_entry  # noqa: E402

OUT = Path.home() / "kripto" / "odev"; OUT.mkdir(parents=True, exist_ok=True)
STATE = OUT / "portfoy.json"
API = "https://fapi.binance.com/fapi/v1/"
CAPITAL, SLOTS, MARGIN_SHARE, STOP_LOSS_OF_MARGIN = 224.0, 8, 0.96, 0.75
FEE, SLIP, H2, M5 = 0.0005, 0.0005, 7_200_000, 300_000
STABLE = {"USDC", "FDUSD", "TUSD", "USDP", "DAI", "USDD", "USD1", "USDE", "USDS", "USDG", "RLUSD", "BFUSD",
          "AEUR", "EUR", "EURI", "USTC", "PAXG", "XAUT", "BTCDOM"}
TR = timezone(timedelta(hours=3))


def get(path, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    with urllib.request.urlopen(f"{API}{path}?{q}", timeout=20) as r:
        return json.loads(r.read())


def klines(symbol, interval, limit=1000, start=None):
    p = dict(symbol=symbol, interval=interval, limit=limit)
    if start: p["startTime"] = start
    return [dict(t=int(b[0]), o=float(b[1]), h=float(b[2]), l=float(b[3]), c=float(b[4]), v=float(b[5]),
                 close_t=int(b[6])) for b in get("klines", **p)]


def closed(rows):
    now = int(time.time() * 1000)
    return [r for r in rows if r["close_t"] < now]


def stamp(ms=None):
    return datetime.fromtimestamp((ms or time.time() * 1000) / 1000, TR).strftime("%d.%m %H:%M")


def load():
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else None


def save(state):
    STATE.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")


def pick():
    if load():
        sys.exit(f"Portfoy zaten var: {STATE} (yeniden secmek icin dosyayi elle silin)")
    cfg = json.loads((ROOT / "config_v5_long_2h.json").read_text(encoding="utf-8"))
    tick = get("ticker/24hr")
    info = {s["symbol"]: s for s in get("exchangeInfo")["symbols"]}
    perp = [t for t in tick if t["symbol"].isascii() and t["symbol"].endswith("USDT") and info.get(t["symbol"], {}).get("contractType") == "PERPETUAL"
            and info[t["symbol"]].get("status") == "TRADING" and t["symbol"][:-4] not in STABLE]
    top = [t["symbol"] for t in sorted(perp, key=lambda t: -float(t["quoteVolume"]))[:100]]
    pct24 = {t["symbol"]: float(t["priceChangePercent"]) / 100 for t in tick}
    btc_raw, cursor = [], int(time.time() * 1000) - 2700 * H2  # ~225 days for the 200-day regime line
    while True:
        batch = klines("BTCUSDT", "2h", 1000, cursor)
        btc_raw += batch
        if len(batch) < 1000: break
        cursor = batch[-1]["t"] + H2
    btc_rows = symmetric_features(closed(btc_raw), cfg)
    btc = btc_rows[-1]; btc7 = btc["c"] / btc_rows[-85]["c"] - 1
    scored = []
    for s in top:
        rows = closed(klines(s, "2h", 1000)) if s != "BTCUSDT" else closed(btc_raw)[-1000:]
        if len(rows) < 200: continue
        f = symmetric_features(rows, cfg)[-1]
        e20, e50, atr, c = f.get("ema20"), f.get("ema50"), f.get("atr"), f["c"]
        if not (e20 and e50 and atr) or abs(pct24.get(s, 0)) > 0.20: continue
        if c > e20 > e50: side, brk = "LONG", f.get("breaks_up", 0)
        elif c < e20 < e50: side, brk = "SHORT", f.get("breaks_down", 0)
        else: continue
        week = c / rows[-85]["c"] - 1; rel = week - btc7
        bot_signal = side == "LONG" and long_entry(f, btc, cfg) is not None
        score = brk + 3 * bot_signal + 10 * (rel if side == "LONG" else -rel)
        scored.append(dict(symbol=s, side=side, score=round(score, 2), breaks=brk, bot_signal=bot_signal,
                           rel7=round(rel, 4), week=round(week, 4), ext_atr=round((c - e20) / atr, 2), stop_frac=round(cfg["atr_multiplier"] * atr / c, 4), atr=atr, signal_close=c, rsi=round(f.get("rsi") or 0, 1)))
        time.sleep(0.05)
    ranked = sorted(scored, key=lambda x: -x["score"])
    # A: the bot's caution kept -- not stretched from EMA20, stop within 15%, not pumped/dumped 30% in a week.
    # B: the same ranking unfiltered (pure momentum: the NIL/MUBARAK type that lost live 22-30 Sep).
    groups = {"A": [x for x in ranked if abs(x["ext_atr"]) <= 1.5 and x["stop_frac"] <= 0.15
                    and abs(x["week"]) <= 0.30][:SLOTS], "B": ranked[:SLOTS]}
    (OUT / "adaylar.json").write_text(json.dumps(ranked, indent=1), encoding="utf-8")
    now = int(time.time() * 1000)
    prices = {p["symbol"]: float(p["price"]) for p in get("ticker/price")}
    margin = CAPITAL * MARGIN_SHARE / SLOTS
    positions = []
    for group, chosen in groups.items():
      for x in chosen:
        entry = prices[x["symbol"]]
        dist = max(cfg["atr_multiplier"] * x["atr"] / entry, 0.03)
        lev = max(1, min(20, int(STOP_LOSS_OF_MARGIN / dist)))  # 1x: a stop beyond 50% would liquidate first at 2x
        sign = 1 if x["side"] == "LONG" else -1
        positions.append(dict(x, group=group, entry=entry, stop=entry * (1 - sign * dist), target=entry * (1 + sign * 2 * dist),
                              be_trigger=entry * (1 + sign * 0.02), stop_pct=round(100 * dist, 2), leverage=lev,
                              margin=round(margin, 2), notional=round(margin * lev, 2), opened=now, status="ACIK",
                              checked=now, be=False))
    state = dict(created=now, capital=CAPITAL, btc_at_open=btc["c"], btc_regime_ma=btc.get("regime_ma"),
                 method=__doc__.split("Method")[1].strip(), candidates=len(scored), positions=positions,
                 vetoes=[], calls=[])
    save(state)
    report(state)


def check(state):
    now = int(time.time() * 1000)
    prices = {p["symbol"]: float(p["price"]) for p in get("ticker/price")}
    for p in state["positions"]:
        p["last"] = prices.get(p["symbol"], p.get("last", p["entry"]))
        if p["status"] != "ACIK": continue
        long = p["side"] == "LONG"
        for b in closed(klines(p["symbol"], "5m", 1000, p["checked"] // M5 * M5)):
            if b["t"] < p["opened"]: continue  # the bar the entry fell inside: its range came partly before
            # inside one bar the stop is assumed to come first (the cautious reading)
            hit_stop = b["l"] <= p["stop"] if long else b["h"] >= p["stop"]
            hit_tgt = b["h"] >= p["target"] if long else b["l"] <= p["target"]
            if hit_stop:
                p.update(status="STOP" if not p["be"] else "GIRISTE CIKTI", exit=p["stop"], exit_t=b["close_t"]); break
            if hit_tgt:
                p.update(status="HEDEF", exit=p["target"], exit_t=b["close_t"]); break
            if not p["be"] and (b["h"] >= p["be_trigger"] if long else b["l"] <= p["be_trigger"]):
                p["be"] = True
                p["stop"] = p["entry"] * (1 + 2 * (FEE + SLIP)) if long else p["entry"] * (1 - 2 * (FEE + SLIP))
            p["checked"] = b["close_t"] + 1
    state["checked"] = now


def pnl(p, price):
    sign = 1 if p["side"] == "LONG" else -1
    gross = sign * (price / p["entry"] - 1) * p["notional"]
    return gross - 2 * (FEE + SLIP) * p["notional"]


def report(state):
    lines = [f"ODEV PORTFOYU (kagit) | acilis {stamp(state['created'])} | sermaye {state['capital']:.0f} USDT | "
             f"guncelleme {stamp(state.get('checked'))}", ""]
    veto_of = lambda p: next((v for v in state["vetoes"] if v["symbol"] == p["symbol"]), None)
    for group, title in (("A", "A: botun filtreleriyle"), ("B", "B: filtresiz momentum (canlida kaybettiren tip)")):
        ps = [p for p in state["positions"] if p.get("group", "A") == group]
        if not ps: continue
        lines.append(title)
        total = 0.0
        for p in ps:
            price = p.get("exit", p.get("last", p["entry"]))
            r = pnl(p, price); total += r
            v = veto_of(p)
            lines.append(f"  {p['symbol']:13} {p['side']:5} {p['leverage']:2d}x marj {p['margin']:.0f} | giris {p['entry']:.6g} "
                         f"stop {p['stop']:.6g} (%{p['stop_pct']}) hedef {p['target']:.6g} | {p['status']:13} "
                         f"fiyat {price:.6g} | {r:+.2f} USDT ({100 * r / p['margin']:+.0f}% marj)"
                         + (" | BE" if p["be"] else "")
                         + (f" | IPTAL {stamp(v['t'])} @{v['price']:.6g} -> iptalle {pnl(p, v['price']):+.2f}" if v else ""))
        worst = sum(pnl(p, p["stop"] if p["status"] == "ACIK" else p["exit"]) for p in ps)
        lines.append(f"  TOPLAM {group}: {total:+.2f} USDT ({100 * total / state['capital']:+.1f}%) | hepsi stop olursa: {worst:+.2f} USDT")
        if any(veto_of(p) for p in ps):
            with_veto = sum(pnl(p, veto_of(p)["price"]) if veto_of(p) else pnl(p, p.get("exit", p.get("last", p["entry"])))
                            for p in ps)
            lines.append(f"  Kullanicinin iptalleriyle olsaydi: {with_veto:+.2f} USDT")
        lines.append("")
    for c in state["calls"]:
        now_btc = float(get("ticker/price", symbol="BTCUSDT")["price"])
        lines.append(f"Kullanici tahmini {stamp(c['t'])}: \"{c['text']}\" | BTC o an {c['btc']:.0f}, simdi {now_btc:.0f} "
                     f"({100 * (now_btc / c['btc'] - 1):+.2f}%)")
    text = "\n".join(lines)
    (OUT / "rapor.txt").write_text(text, encoding="utf-8")
    print(text)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "izle"
    if cmd == "sec": return pick()
    state = load()
    if not state: sys.exit("Once: python tools/odev.py sec")
    if cmd == "iptal":
        sym = sys.argv[2].upper(); sym = sym if sym.endswith("USDT") else sym + "USDT"
        price = float(get("ticker/price", symbol=sym)["price"])
        state["vetoes"].append(dict(symbol=sym, t=int(time.time() * 1000), price=price))
    elif cmd == "tahmin":
        btc = float(get("ticker/price", symbol="BTCUSDT")["price"])
        state["calls"].append(dict(text=" ".join(sys.argv[2:]), t=int(time.time() * 1000), btc=btc))
    if cmd == "dongu":
        every = int(sys.argv[2]) if len(sys.argv) > 2 else 15
        while True:
            state = load()  # re-read: a veto or call noted from another window must not be overwritten
            check(state); save(state); report(state); print(flush=True)
            time.sleep(every * 60)
    check(state); save(state); report(state)


if __name__ == "__main__":
    main()
