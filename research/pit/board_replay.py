"""The direction board's verdicts replayed on 12 months of 5m futures data (user, 1 Oct 2026: "would shorting
everything the board calls LONG make money?"). Same rules as tools/yon_panosu.py:
  per timeframe (5m, 1h, 4h, 1d) vote = (price > EMA200) + (EMA50 > EMA200) + Ichimoku cloud position, each +-1
  score = 2 x (1d + 4h votes) + (1h + 5m votes) + 2 if the coin beat BTC by 2%+ over 24h (-2 if it lagged)
  GUCLU LONG >= 14, LONG >= 7, SHORT <= -7, GUCLU SHORT <= -14, NOTR otherwise
Approximation: the 1h/4h/1d EMAs and cloud come from the last CLOSED bar of that timeframe, compared with the
current 5m close (the board uses the still-open candle). Sampled every 15 minutes, 27 coins, Sep 2025 - Aug 2026.
For each verdict and horizon (15/30/60 min): share right, mean move in the verdict's direction, the same after
0.10% costs, the move against the market (all coins' mean move at that moment), and the INVERSE trade."""
import time
from collections import defaultdict
from bias_scalper import COINS, load, ema

TF = {"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
W = {"5m": 1, "1h": 1, "4h": 2, "1d": 2}
HOR = {15: 3, 30: 6, 60: 12}
COST = 0.10


def tf_bars(R, ms):
    out, cur = [], None
    for t, o, h, l, c, v in R:
        b = t // ms
        if cur is None or cur[0] != b:
            if cur: out.append(cur)
            cur = [b, h, l, c]
        else:
            cur[1] = max(cur[1], h); cur[2] = min(cur[2], l); cur[3] = c
    if cur: out.append(cur)
    return out


def indicators(H, L, C):
    """Per bar j: ema50, ema200, cloud top, cloud bottom (the cloud drawn AT bar j, i.e. built 26 bars earlier)."""
    e50, e200 = ema(C, 50), ema(C, 200)
    def mid(n, j):
        return (max(H[j - n + 1:j + 1]) + min(L[j - n + 1:j + 1])) / 2
    top, bot = [None] * len(C), [None] * len(C)
    for j in range(52 + 26, len(C)):
        k = j - 26
        a = (mid(9, k) + mid(26, k)) / 2; b = mid(52, k)
        top[j], bot[j] = max(a, b), min(a, b)
    return e50, e200, top, bot


def vote(price, e50, e200, top, bot):
    if top is None:
        return None
    cloud = 1 if price > top else -1 if price < bot else 0
    return (1 if price > e200 else -1) + (1 if e50 > e200 else -1) + cloud


def verdict(score):
    return ("GUCLU LONG" if score >= 14 else "LONG" if score >= 7 else
            "GUCLU SHORT" if score <= -14 else "SHORT" if score <= -7 else "NOTR")


if __name__ == "__main__":
    t0 = time.time()
    btc = {r[0]: r[4] for r in load("BTCUSDT")}
    samples = defaultdict(dict)          # t -> coin -> (verdict, {h: move %})
    for coin in COINS:
        R = load(coin)
        if len(R) < 30000:
            continue
        T = [r[0] for r in R]; C = [r[4] for r in R]
        i5 = indicators([r[2] for r in R], [r[3] for r in R], C)
        tfs = {}
        for name, ms in TF.items():
            bars = tf_bars(R, ms)
            ind = indicators([b[1] for b in bars], [b[2] for b in bars], [b[3] for b in bars])
            tfs[name] = ({b[0]: j for j, b in enumerate(bars)}, ind, ms)
        for k in range(300 * 12, len(R) - 13, 3):
            if T[k + 12] - T[k] != 12 * 300_000:
                continue
            price = C[k]
            v5 = vote(price, i5[0][k], i5[1][k], i5[2][k], i5[3][k])
            votes = {"5m": v5}
            for name, (index, (e50, e200, top, bot), ms) in tfs.items():
                j = index.get((T[k] + 300_000) // ms - 1)        # last bar closed by the end of this 5m bar
                votes[name] = None if j is None else vote(price, e50[j], e200[j], top[j], bot[j])
            if None in votes.values():
                continue
            t_close = T[k] + 300_000
            b_now, b_then = btc.get(T[k]), btc.get(T[k] - 86_400_000)
            if not b_now or not b_then or k < 288:
                continue
            rel = (C[k] / C[k - 288] - 1) - (b_now / b_then - 1)
            score = sum(W[n] * votes[n] for n in W) + (2 if rel > 0.02 else -2 if rel < -0.02 else 0)
            samples[t_close][coin] = (verdict(score), {h: (C[k + n] / price - 1) * 100 for h, n in HOR.items()})
        print(f"  {coin} ({round(time.time()-t0)} sn)", flush=True)
    stats = defaultdict(list)            # (verdict, h) -> list of (move, move - market)
    for t, coins in samples.items():
        if len(coins) < 10:
            continue
        for h in HOR:
            mkt = sum(m[h] for _, m in coins.values()) / len(coins)
            for v, m in coins.values():
                stats[(v, h)].append((m[h], m[h] - mkt))
    print(f"\n{len(samples)} an x ~{sum(len(c) for c in samples.values())//max(1,len(samples))} coin, her 15 dk | "
          f"Eyl 2025 - Agu 2026 | {round(time.time()-t0)} sn\n")
    for v in ("GUCLU LONG", "LONG", "NOTR", "SHORT", "GUCLU SHORT"):
        side = 1 if "LONG" in v else -1 if "SHORT" in v else 0
        for h in HOR:
            xs = stats.get((v, h))
            if not xs:
                continue
            n = len(xs)
            if side:
                d = [side * m for m, _ in xs]; r = [side * x for _, x in xs]
                print(f"{v:11} {h:2d} dk | {n:6d} olcum | dogru %{100*sum(x > 0 for x in d)/n:4.1f} | "
                      f"ort {sum(d)/n:+.3f}% | masraf sonrasi {sum(d)/n - COST:+.3f}% | piyasaya gore {sum(r)/n:+.3f}% | "
                      f"TERSI masraf sonrasi {-sum(d)/n - COST:+.3f}%")
            else:
                print(f"{v:11} {h:2d} dk | {n:6d} olcum | yukselen %{100*sum(m > 0 for m, _ in xs)/n:4.1f} | "
                      f"ort {sum(m for m, _ in xs)/n:+.3f}% | piyasaya gore {sum(x for _, x in xs)/n:+.3f}%")
