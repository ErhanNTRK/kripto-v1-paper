"""Long BTCDOMUSDT (Binance's BTC-dominance perpetual: BTC against a weighted basket of top alts) as the one-contract
version of "long BTC + short alts" (5 Oct 2026). Daily closes since listing (Jun 2021), REAL funding paid by the long
(every 8h payment summed per day), 0.04% a side when entering/leaving. Hold always, or only while the live ETH/BTC
gate is closed / open (checked each day), at 1x and 2x. Note: the data start after the Jan-May 2021 alt mania, when
BTC dominance fell from ~70% to ~40%; that kind of period would hit this position hard."""
import json, urllib.request
from datetime import datetime, timezone
DAY = 86_400_000
get = lambda u: json.load(urllib.request.urlopen(u))

def daily(sym):
    out, start = {}, 1_577_836_800_000
    while True:
        k = get(f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1d&startTime={start}&limit=1500")
        for r in k: out[int(r[0]) // DAY] = float(r[4])
        if len(k) < 1500: break
        start = int(k[-1][0]) + DAY
    return out

dom, btc, eth = daily("BTCDOMUSDT"), daily("BTCUSDT"), daily("ETHUSDT")
fund, start = {}, 1_609_459_200_000
while True:
    k = get(f"https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCDOMUSDT&startTime={start}&limit=1000")
    for r in k:
        d = int(r["fundingTime"]) // DAY; fund[d] = fund.get(d, 0.0) + float(r["fundingRate"])
    if len(k) < 1000: break
    start = int(k[-1]["fundingTime"]) + 1
days = sorted(set(dom) & set(btc) & set(eth))
gate, m = {}, None
for d in days:
    r = eth[d] / btc[d]; m = r if m is None else m + (r - m) * 2 / 51; gate[d] = r > m
for mode in ("her zaman", "kapi kapaliyken", "kapi acikken"):
    for lev in (1, 2):
        eq, peak, mdd, yearly, inpos = 1.0, 1.0, 0.0, {}, False
        for prev, d in zip(days, days[1:]):
            want = mode == "her zaman" or (mode == "kapi kapaliyken") != gate[prev]
            if want != inpos: eq *= 1 - 0.0004 * lev; inpos = want
            if inpos:
                r = lev * (dom[d] / dom[prev] - 1 - fund.get(d, 0.0))
                eq *= 1 + r
            peak = max(peak, eq); mdd = max(mdd, 1 - eq / peak)
            y = datetime.fromtimestamp(d * DAY / 1000, timezone.utc).year
            yearly.setdefault(y, [eq, eq]); yearly[y][1] = eq
        ys = " ".join(f"{y}:x{b / a:.2f}" for y, (a, b) in sorted(yearly.items()))
        print(f"BTCDOM long {lev}x, {mode:15}: 100$ -> {100 * eq:5.0f} | en derin dusus %{100 * mdd:.0f} | {ys}")
