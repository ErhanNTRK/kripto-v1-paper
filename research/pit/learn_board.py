"""Can the direction board learn from its mistakes? (user, 2 Oct 2026). Walk-forward test on 12 months of 5m data,
27 coins, every 15 minutes: each month a model is trained ONLY on the months before it (from its errors: what the
features said vs what the price then did) and trades the next, unseen month.
Features per coin and moment: the board's four timeframe votes and score, strength vs BTC over 24h, the coin's
1h / 4h / 24h returns, its rise from the 24h low (pump), 1h RSI, last hour's volume vs the day's, BTC's 1h and
24h returns. Target: the coin's move over the next 15 / 60 minutes against all coins' mean move (which coin does
better than the market).
Trading rule (market-neutral, so the market's own direction does not decide the result): long the 3 coins the
model ranks best, short the 3 it ranks worst, held 15 or 60 minutes; cost 0.10% per leg round trip.
Compared with the board's fixed rules (long the 3 highest scores, short the 3 lowest) and their inverse.
Run with the research venv (numpy, scikit-learn)."""
import time
from collections import defaultdict
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from bias_scalper import COINS, load, ema
from board_replay import TF, W, tf_bars, indicators, vote

HOLD = {15: 3, 60: 12}
COST = 0.10


def rsi(closes, n=14):
    g = l = 0.0
    for a, b in zip(closes[-n - 1:-1], closes[-n:]):
        d = b - a; g += max(d, 0); l += max(-d, 0)
    return 100.0 if l == 0 else 100 - 100 / (1 + g / l)


if __name__ == "__main__":
    t0 = time.time()
    B = load("BTCUSDT"); btc = {r[0]: r[4] for r in B}
    rows = defaultdict(dict)                       # t -> coin -> (features, {h: move %})
    for coin in COINS:
        R = load(coin)
        if len(R) < 30000: continue
        T = [r[0] for r in R]; C = [r[4] for r in R]; L_ = [r[3] for r in R]; V = [r[5] for r in R]
        i5 = indicators([r[2] for r in R], L_, C)
        tfs = {}
        for name, ms in TF.items():
            bars = tf_bars(R, ms)
            tfs[name] = ({b[0]: j for j, b in enumerate(bars)}, indicators([b[1] for b in bars], [b[2] for b in bars], [b[3] for b in bars]), ms)
        c1h = [C[k] for k in range(0, len(C))]
        for k in range(3600, len(R) - 13, 3):
            if T[k + 12] - T[k] != 3_600_000: continue
            p = C[k]
            votes = {"5m": vote(p, i5[0][k], i5[1][k], i5[2][k], i5[3][k])}
            for name, (index, (e50, e200, top, bot), ms) in tfs.items():
                j = index.get((T[k] + 300_000) // ms - 1)
                votes[name] = None if j is None else vote(p, e50[j], e200[j], top[j], bot[j])
            b0, b1, b24 = btc.get(T[k]), btc.get(T[k] - 3_600_000), btc.get(T[k] - 86_400_000)
            if None in votes.values() or not (b0 and b1 and b24): continue
            rel24 = (p / C[k - 288] - 1) - (b0 / b24 - 1)
            score = sum(W[n] * votes[n] for n in W) + (2 if rel24 > 0.02 else -2 if rel24 < -0.02 else 0)
            hourly = C[k - 180:k + 1:12]
            vol1 = sum(V[k - 11:k + 1]); vol24 = sum(V[k - 287:k + 1]) / 24
            feats = [votes["5m"], votes["1h"], votes["4h"], votes["1d"], score, rel24 * 100,
                     (p / C[k - 12] - 1) * 100, (p / C[k - 48] - 1) * 100, (p / C[k - 288] - 1) * 100,
                     (p / min(L_[k - 287:k + 1]) - 1) * 100, rsi(hourly), vol1 / vol24 if vol24 else 1,
                     (b0 / b1 - 1) * 100, (b0 / b24 - 1) * 100]
            rows[T[k] + 300_000][coin] = (feats, {h: (C[k + n] / p - 1) * 100 for h, n in HOLD.items()})
        print(f"  {coin} ({round(time.time()-t0)} sn)", flush=True)
    times = sorted(t for t, c in rows.items() if len(c) >= 15)
    month = lambda t: time.strftime("%Y-%m", time.gmtime(t / 1000))
    months = sorted({month(t) for t in times})
    print(f"\n{len(times)} an, {len(months)} ay | {round(time.time()-t0)} sn\n", flush=True)
    for h in HOLD:
        X, Y, M_, TT = [], [], [], []
        for t in times:
            mkt = np.mean([m[h] for _, m in rows[t].values()])
            for coin, (f, m) in rows[t].items():
                X.append(f); Y.append(m[h] - mkt); M_.append(month(t)); TT.append(t)
        X, Y, M_, TT = np.array(X), np.array(Y), np.array(M_), np.array(TT)
        print(f"=== {h} dk tutma, her 15 dk 3 long + 3 short (piyasadan bagimsiz), masraf dahil ===")
        res = defaultdict(list)
        for mi, mo in enumerate(months):
            if mi < 3: continue                       # learn from at least 3 months first
            tr, te = M_ < mo, M_ == mo
            models = {"ogrenen (dogrusal)": Ridge(alpha=10.0).fit(X[tr], Y[tr]),
                      "ogrenen (agac)": HistGradientBoostingRegressor(max_iter=150, max_depth=4, learning_rate=0.05).fit(X[tr], Y[tr])}
            preds = {n: m.predict(X[te]) for n, m in models.items()}
            preds["panonun sabit kurali"] = X[te][:, 4]
            preds["panonun tersi"] = -X[te][:, 4]
            tt, yy = TT[te], Y[te]
            for n, pr in preds.items():
                spreads = []
                for t in np.unique(tt)[::h // 15]:          # non-overlapping holds
                    idx = np.where(tt == t)[0]
                    if len(idx) < 10: continue
                    o = idx[np.argsort(pr[idx], kind="stable")]
                    spreads.append(yy[o[-3:]].mean() - yy[o[:3]].mean() - 2 * COST)
                res[n].append((mo, np.mean(spreads), len(spreads)))
        for n, lst in res.items():
            allm = [s for _, s, _ in lst]
            win = sum(s > 0 for s in allm)
            print(f"{n:22} | islem basi ort {np.mean(allm):+.3f}% | kazandiran ay {win}/{len(allm)} | "
                  + " ".join(f"{mo[5:]}:{s:+.2f}" for mo, s, _ in lst))
        print()
    print(f"BITTI ({round(time.time()-t0)} sn)")
