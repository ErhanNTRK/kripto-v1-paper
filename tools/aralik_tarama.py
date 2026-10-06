"""Read-only scan for coins that have been ranging sideways (public Binance futures data, no keys, no orders).

For each liquid USDT perp: 4H bars over the last LOOKBACK days. A coin counts as ranging when
  - the band (5th pct of lows .. 95th pct of highs) is 8-35% wide,
  - the price has visited the bottom 20% of the band and the top 20% of the band at least MIN_TOUCH times each
    (separate visits, not consecutive bars),
  - the net drift across the window is small relative to the band, and
  - the price is still inside the band (a break means the range is over).
Prints the plan the user asked for: long near the floor, short near the ceiling, stop outside the band, target the middle.

Usage: PYTHONIOENCODING=utf-8 ~/kripto/venv/Scripts/python.exe tools/aralik_tarama.py [lookback_days] [top_n]
"""
import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = "https://fapi.binance.com"
LOOKBACK = int(sys.argv[1]) if len(sys.argv) > 1 else 45
TOP_N = int(sys.argv[2]) if len(sys.argv) > 2 else 150
MIN_TOUCH = 2
ZONE = 0.20
MIN_WIDTH, MAX_WIDTH = 0.08, 0.35
MAX_DRIFT = 0.35          # |first-to-last close| / band width
STOP_BEYOND = 0.03        # stop this far outside the band


def get(path, retries=3):
    for i in range(retries):
        try:
            req = urllib.request.Request(BASE + path, headers={"User-Agent": "kripto-v1"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        except Exception:
            time.sleep(1 + i)
    return None


def universe():
    info = get("/fapi/v1/exchangeInfo")
    ok = {s["symbol"] for s in info["symbols"] if s["quoteAsset"] == "USDT" and s["contractType"] == "PERPETUAL"
          and s["status"] == "TRADING"}
    rows = [r for r in get("/fapi/v1/ticker/24hr") if r["symbol"] in ok]
    rows.sort(key=lambda r: -float(r["quoteVolume"]))
    return [r["symbol"] for r in rows[:TOP_N]]


def pct(values, p):
    v = sorted(values)
    return v[min(len(v) - 1, max(0, int(round(p * (len(v) - 1)))))]


def visits(series, test):
    n, prev = 0, False
    for x in series:
        hit = test(x)
        if hit and not prev:
            n += 1
        prev = hit
    return n


def analyse(symbol):
    rows = get(f"/fapi/v1/klines?symbol={symbol}&interval=4h&limit={LOOKBACK * 6}")
    if not rows or len(rows) < LOOKBACK * 5:
        return None
    hi_s = [float(r[2]) for r in rows]
    lo_s = [float(r[3]) for r in rows]
    cl = [float(r[4]) for r in rows]
    top, bot = pct(hi_s, 0.95), pct(lo_s, 0.05)
    mid = (top + bot) / 2
    width = (top - bot) / mid
    if not (MIN_WIDTH <= width <= MAX_WIDTH):
        return None
    if abs(cl[-1] - cl[0]) / (top - bot) > MAX_DRIFT:
        return None
    last = cl[-1]
    if last > top * 1.01 or last < bot * 0.99:
        return None
    floor_lim, ceil_lim = bot + ZONE * (top - bot), top - ZONE * (top - bot)
    low_visits = visits(lo_s, lambda x: x <= floor_lim)
    high_visits = visits(hi_s, lambda x: x >= ceil_lim)
    if low_visits < MIN_TOUCH or high_visits < MIN_TOUCH:
        return None
    return {"symbol": symbol, "bot": bot, "top": top, "mid": mid, "width": width, "last": last,
            "pos": (last - bot) / (top - bot), "low_visits": low_visits, "high_visits": high_visits,
            "floor_lim": floor_lim, "ceil_lim": ceil_lim, "bars": len(rows)}


def fmt(x):
    return f"{x:.6g}"


def main():
    symbols = universe()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = [r for r in pool.map(analyse, symbols) if r]
    results.sort(key=lambda r: -(min(r["low_visits"], r["high_visits"]) * 100 - abs(r["pos"] - 0.5) * 10))
    print(f"ARALIK TARAMA  son {LOOKBACK} gun, 4H mum, ilk {len(symbols)} hacimli USDT vadeli -> {len(results)} aralik\n")
    print(f"{'coin':12} {'alt':>10} {'ust':>10} {'genislik':>8} {'simdi':>10} {'konum':>6} {'dip/tepe':>9}  bolge")
    for r in results:
        zone = "ZEMIN (long bolgesi)" if r["last"] <= r["floor_lim"] else "TAVAN (short bolgesi)" if r["last"] >= r["ceil_lim"] else "orta"
        print(f"{r['symbol']:12} {fmt(r['bot']):>10} {fmt(r['top']):>10} {r['width']*100:7.0f}% {fmt(r['last']):>10} "
              f"{r['pos']*100:5.0f}% {r['low_visits']:>4}/{r['high_visits']:<4}  {zone}")
    print("\nPLAN (aralik bozulmadikca):  long <= alt+%20 bant  | short >= ust-%20 bant | stop: bandin %3 disi | hedef: orta cizgi")
    for r in results:
        print(f"{r['symbol']:12} long {fmt(r['bot'])}-{fmt(r['floor_lim'])} stop {fmt(r['bot']*(1-STOP_BEYOND))} hedef {fmt(r['mid'])}"
              f"  ||  short {fmt(r['ceil_lim'])}-{fmt(r['top'])} stop {fmt(r['top']*(1+STOP_BEYOND))} hedef {fmt(r['mid'])}")


if __name__ == "__main__":
    main()
