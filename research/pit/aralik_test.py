"""Walk-forward test of the range strategy (6 Oct 2026). Public Binance futures 4H data only, no orders.

At every 4H close: band = 5th pct of the last 270 lows .. 95th pct of highs; a range needs width 8-35%, small drift,
>=2 separate visits to the bottom 20% and to the top 20%, and the close still inside the band.
Signal: close in the bottom 20% -> long, top 20% -> short, entered at the NEXT bar's open (no lookahead).
Stop: STOP_BEYOND outside the band; target: band middle; both fixed at entry; time exit after MAX_HOLD bars.
If a bar touches both stop and target the stop is assumed first. Round trip costs COST (fees + slippage).
Control: the same exits, but entered when the close is in the MIDDLE 40-60% of the band, long and short alike
(if the floor/ceiling matters, the control should do clearly worse).
Caveat: universe = today's top-volume perps (survivorship bias, flatters a long-only view slightly; shorts and longs here
are symmetric so the bias mostly cancels).

  python research/pit/aralik_test.py [years] [top_n]"""
import json, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import bisect, statistics

YEARS = float(sys.argv[1]) if len(sys.argv) > 1 else 2
TOP_N = int(sys.argv[2]) if len(sys.argv) > 2 else 120
BASE = "https://fapi.binance.com"
H4 = 14_400_000
WIN = 270
ZONE, MIN_W, MAX_W, MAX_DRIFT, STOP_BEYOND = 0.20, 0.08, 0.35, 0.35, 0.03
INVERSE = len(sys.argv) > 3 and sys.argv[3] == 'ters'
MAX_HOLD = 6 * 20          # 20 days
COST = 0.002
CACHE = Path(__file__).with_name("_aralik_cache.json")


def get(path):
    for i in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(BASE + path, headers={"User-Agent": "k"}), timeout=25) as r:
                return json.load(r)
        except Exception:
            time.sleep(1 + i)
    return None


def history(symbol, bars):
    out, end = [], int(time.time() * 1000)
    while len(out) < bars:
        rows = get(f"/fapi/v1/klines?symbol={symbol}&interval=4h&limit=1500&endTime={end}")
        if not rows:
            break
        out = rows + out
        end = int(rows[0][0]) - 1
        if len(rows) < 1500:
            break
    return [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])] for r in out[-bars:]]


def load():
    if CACHE.exists() and time.time() - CACHE.stat().st_mtime < 86400:
        return json.loads(CACHE.read_text())
    info = get("/fapi/v1/exchangeInfo")
    ok = {s["symbol"] for s in info["symbols"] if s["quoteAsset"] == "USDT" and s["contractType"] == "PERPETUAL"}
    rows = sorted((r for r in get("/fapi/v1/ticker/24hr") if r["symbol"] in ok), key=lambda r: -float(r["quoteVolume"]))
    syms = [r["symbol"] for r in rows[:TOP_N]]
    bars = int(YEARS * 365 * 6) + WIN
    with ThreadPoolExecutor(max_workers=8) as pool:
        data = dict(zip(syms, pool.map(lambda s: history(s, bars), syms)))
    CACHE.write_text(json.dumps(data))
    return data


def visits(values, test):
    n, prev = 0, False
    for x in values:
        hit = test(x)
        if hit and not prev:
            n += 1
        prev = hit
    return n


def mean(v): return sum(v) / len(v)


def run_symbol(rows):
    t, o, h, l, c = ([r[k] for r in rows] for k in range(5))
    n = len(rows)
    trades = []
    if n < WIN + 50:
        return trades
    sh, sl = sorted(h[:WIN]), sorted(l[:WIN])
    hi, lo = [], []                                  # index k: window ending at bar k+WIN-1
    q = lambda arr, p: arr[min(WIN - 1, max(0, int(round(p * (WIN - 1)))))]
    for k in range(n - WIN + 1):
        if k:
            sh.pop(bisect.bisect_left(sh, h[k - 1])); bisect.insort(sh, h[k + WIN - 1])
            sl.pop(bisect.bisect_left(sl, l[k - 1])); bisect.insort(sl, l[k + WIN - 1])
        hi.append(q(sh, 0.95)); lo.append(q(sl, 0.05))
    free_after = 0
    for k in range(len(hi) - 1):
        i = k + WIN - 1                              # signal bar (close known)
        if i < free_after:
            continue
        top, bot = hi[k], lo[k]
        mid = (top + bot) / 2
        w = (top - bot) / mid
        if not (MIN_W <= w <= MAX_W) or abs(c[i] - c[i - WIN + 1]) / (top - bot) > MAX_DRIFT:
            continue
        pos = (c[i] - bot) / (top - bot)
        if pos < -0.03 or pos > 1.03:
            continue
        if pos <= ZONE: kind, side = "zone", 1
        elif pos >= 1 - ZONE: kind, side = "zone", -1
        elif 0.4 <= pos <= 0.6: kind, side = "ctrl", 1 if pos < 0.5 else -1   # control follows the same side by half
        else:
            continue
        fl, ce = bot + ZONE * (top - bot), top - ZONE * (top - bot)
        wl, wh = l[i - WIN + 1:i + 1], h[i - WIN + 1:i + 1]
        if visits(wl, lambda x: x <= fl) < 2 or visits(wh, lambda x: x >= ce) < 2:
            continue
        entry = o[i + 1]
        stop = bot * (1 - STOP_BEYOND) if side == 1 else top * (1 + STOP_BEYOND)
        target = mid
        if kind == "ctrl":                            # control: same stop distance and target size as a floor trade
            stop = entry * (1 - (entry - bot * (1 - STOP_BEYOND)) / entry * 1) if side == 1 else entry * (1 + (top * (1 + STOP_BEYOND) - entry) / entry)
            target = entry * (1 + side * 0.5 * (top - bot) / entry * 0.5)
        if INVERSE and kind == "zone":                # trade the breakout instead: stop back at the middle, same size target beyond
            side = -side
            stop = mid
            target = entry + side * abs(entry - mid)
        if side == 1 and not (stop < entry < target): continue
        if side == -1 and not (target < entry < stop): continue
        exit_px, j = None, i + 1
        for j in range(i + 1, min(n, i + 1 + MAX_HOLD)):
            if side == 1:
                if l[j] <= stop: exit_px = stop; break
                if h[j] >= target: exit_px = target; break
            else:
                if h[j] >= stop: exit_px = stop; break
                if l[j] <= target: exit_px = target; break
        else:
            exit_px = c[min(n - 1, i + MAX_HOLD)]; j = min(n - 1, i + MAX_HOLD)
        ret = side * (exit_px / entry - 1) - COST
        trades.append({"t": int(t[i + 1]), "kind": kind, "side": side, "ret": ret, "bars": j - i,
                       "risk": abs(entry - stop) / entry})
        free_after = j + 1
    return trades


def summarize(label, ts):
    if not ts:
        print(f"{label:28} islem yok"); return
    r = [x["ret"] for x in ts]
    wins, losses = sum(x for x in r if x > 0), -sum(x for x in r if x <= 0)
    print(f"{label:28} n={len(r):5d}  kazanma %{100*mean([x > 0 for x in r]):4.0f}  ort %{100*mean(r):+.2f}  medyan %{100*statistics.median(r):+.2f}  "
          f"PF {wins/losses if losses else float('inf'):.2f}  ort stop %{100*mean([x['risk'] for x in ts]):.1f}  ort sure {mean([x['bars'] for x in ts])/6:.1f}g")


def main():
    data = load()
    allt = []
    for s, rows in data.items():
        for x in run_symbol(rows):
            x["sym"] = s; allt.append(x)
    print(f"ARALIK TESTI  {len(data)} coin, ~{YEARS:.0f} yil, 4H, masraf %{100*COST:.1f}/tur, sonraki mum acilisindan giris\n")
    z = [x for x in allt if x["kind"] == "zone"]
    summarize("TUMU (zemin+tavan)", z)
    summarize("  long (zemin)", [x for x in z if x["side"] == 1])
    summarize("  short (tavan)", [x for x in z if x["side"] == -1])
    summarize("KONTROL (orta bant)", [x for x in allt if x["kind"] == "ctrl"])
    print("\nyil/yarim yil:")
    import datetime as dt
    groups = {}
    for x in z:
        d = dt.datetime.utcfromtimestamp(x["t"] / 1000)
        groups.setdefault(f"{d.year}-H{1 if d.month <= 6 else 2}", []).append(x)
    for k in sorted(groups): summarize("  " + k, groups[k])
    months = {}
    for x in z:
        months.setdefault(dt.datetime.utcfromtimestamp(x["t"] / 1000).strftime("%Y-%m"), []).append(x["ret"])
    neg = sum(1 for v in months.values() if sum(v) < 0)
    print(f"\nay bazinda toplam getiri negatif olan ay: {neg}/{len(months)}")
    stops = [x for x in z if x["ret"] < -0.02]
    print(f"stop/buyuk kayip orani: %{100*len(stops)/max(1,len(z)):.0f}  (ort kayip %{100*mean([x['ret'] for x in stops]) if stops else 0:.1f})")


if __name__ == "__main__":
    main()
