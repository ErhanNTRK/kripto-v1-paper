"""Direction-aware fast trading, long AND short (user, 30 Sep 2026: "form a view of each coin's direction, then a
mix of longs and shorts"). 5m futures bars, 29 large coins, Sep 2025 - Aug 2026, closed bars only.

Each coin's direction at every bar:
  UP    close > EMA(2 days) > EMA(8 days), the 2-day EMA rising over the last hour, and the coin beat BTC over 24h
  DOWN  the mirror (and lagged BTC over 24h)
  none  otherwise: no trade
  +btc  variant: longs only while BTC is itself UP-ish (close > its 2-day EMA), shorts only while it is below
Entry trigger, only in the coin's direction:
  pullback  price touched the 1-hour EMA (EMA 12 bars) in the last 3 bars, then a bar closes beyond the
            previous bar's high (long) / low (short)
  breakout  volume >= 3x the 4-hour average and a close beyond the last hour's high / low
Exit: take-profit / stop / time limit, entry at the next bar's open, stop checked first inside a bar.
Portfolio: at most 10 open, each position's notional = equity (10% margin at 10x); cost 0.10% per round trip
(market orders) and 0.06% (limit entries)."""
import csv, glob, io, time, zipfile
from pathlib import Path

DATA = Path(r"C:\Users\ASUS-PC\kripto\arastirma-verisi\fut5m")
COINS = ["ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "BNBUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "SUIUSDT",
         "LTCUSDT", "NEARUSDT", "APTUSDT", "WLDUSDT", "ENAUSDT", "TAOUSDT", "FETUSDT", "ARBUSDT", "OPUSDT", "HYPEUSDT",
         "PEPEUSDT", "WIFUSDT", "ONDOUSDT", "SEIUSDT", "TIAUSDT", "INJUSDT", "AAVEUSDT", "DOTUSDT", "FILUSDT", "CAKEUSDT"]
MONTHS = [f"2025-{m:02d}" for m in range(9, 13)] + [f"2026-{m:02d}" for m in range(1, 9)]
HALF = 1_772_323_200_000          # 1 Mar 2026: first vs second six months


def load(c):
    rows = []
    for m in MONTHS:
        for f in glob.glob(str(DATA / f"{c}-5m-{m}.zip")):
            z = zipfile.ZipFile(f)
            for r in csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]))):
                if r[0].isdigit():
                    rows.append((int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])))
    return rows


def ema(xs, n):
    out, e, k = [], None, 2 / (n + 1)
    for x in xs:
        e = x if e is None else e + (x - e) * k
        out.append(e)
    return out


def signals(R, btc, use_btc):
    C = [r[4] for r in R]; T = [r[0] for r in R]; V = [r[5] for r in R]
    e1h, e2d, e8d = ema(C, 12), ema(C, 576), ema(C, 2304)
    out = {"pullback": [], "breakout": []}
    vs = sum(V[:48])
    for i in range(2400, len(R) - 50):
        vs += V[i - 1] - V[i - 49]
        b = btc.get(T[i]); b24 = btc.get(T[i] - 86_400_000); be = btc.get(("e", T[i]))
        if not b or not b24 or be is None:
            continue
        rel = (C[i] / C[i - 288] - 1) - (b / b24 - 1)
        up = C[i] > e2d[i] > e8d[i] and e2d[i] > e2d[i - 12] and rel > 0
        down = C[i] < e2d[i] < e8d[i] and e2d[i] < e2d[i - 12] and rel < 0
        if use_btc:
            up &= b > be
            down &= b < be
        if not (up or down):
            continue
        side = 1 if up else -1
        o, h, l, c = R[i][1], R[i][2], R[i][3], R[i][4]
        touched = any((R[j][3] <= e1h[j]) if up else (R[j][2] >= e1h[j]) for j in range(i - 3, i))
        if touched and ((c > R[i - 1][2]) if up else (c < R[i - 1][3])):
            out["pullback"].append((i, side))
        top, bot = max(x[2] for x in R[i - 12:i]), min(x[3] for x in R[i - 12:i])
        if V[i] >= 3 * vs / 48 and ((c > top) if up else (c < bot)):
            out["breakout"].append((i, side))
    return out


def outcome(R, i, side, tp, sl, hold):
    e = R[i + 1][1]
    for j in range(i + 1, min(len(R), i + 1 + hold)):
        hi, lo = R[j][2], R[j][3]
        if side == 1:
            if lo <= e * (1 - sl): return -sl, R[j][0]
            if hi >= e * (1 + tp): return tp, R[j][0]
        else:
            if hi >= e * (1 + sl): return -sl, R[j][0]
            if lo <= e * (1 - tp): return tp, R[j][0]
    j = min(len(R) - 1, i + hold)
    return side * (R[j][4] / e - 1), R[j][0]


def portfolio(trades, cost):
    eq, open_ = 100.0, []
    for ti, to, r, _ in sorted(trades):
        still = []
        for x in open_:
            if x[0] <= ti: eq += x[1]
            else: still.append(x)
        open_ = still
        if len(open_) < 10 and eq > 1:
            open_.append((to, eq * (r - cost)))
    return eq + sum(x[1] for x in open_)


if __name__ == "__main__":
    t0 = time.time()
    B = load("BTCUSDT"); bc = [r[4] for r in B]; be2 = ema(bc, 576)
    btc = {r[0]: r[4] for r in B}; btc.update({("e", r[0]): e for r, e in zip(B, be2)})
    data = {c: load(c) for c in COINS}
    data = {c: R for c, R in data.items() if len(R) > 20000}
    print(f"{len(data)} coin yuklendi ({round(time.time()-t0)} sn)", flush=True)
    EXITS = [(0.01, 0.01, 12), (0.015, 0.01, 24), (0.02, 0.01, 48), (0.02, 0.02, 48), (0.03, 0.015, 96)]
    for use_btc in (False, True):
        sigs = {c: signals(R, btc, use_btc) for c, R in data.items()}
        for trig in ("pullback", "breakout"):
            for tp, sl, hold in EXITS:
                trades = []
                for c, R in data.items():
                    last = -999
                    for i, side in sigs[c][trig]:
                        if i - last < 6:            # one entry per coin per 30 minutes
                            continue
                        last = i
                        r, t_out = outcome(R, i, side, tp, sl, hold)
                        trades.append((R[i + 1][0], t_out, r, side))
                if not trades:
                    continue
                def stats(ts, cost):
                    n = len(ts); net = [x[2] - cost for x in ts]
                    gw = sum(x for x in net if x > 0); gl = -sum(x for x in net if x <= 0) or 1e-9
                    return n, 100 * sum(x > 0 for x in net) / n, 100 * sum(net) / n, gw / gl
                n, w, a, pf = stats(trades, 0.001)
                L = [x for x in trades if x[3] == 1]; S_ = [x for x in trades if x[3] == -1]
                h1 = [x for x in trades if x[0] < HALF]; h2 = [x for x in trades if x[0] >= HALF]
                pf_l = stats(L, 0.001)[3] if L else 0; pf_s = stats(S_, 0.001)[3] if S_ else 0
                print(f"{'BTC uyumlu' if use_btc else 'BTC serbest':10} {trig:8} hedef %{100*tp:.1f} stop %{100*sl:.1f} "
                      f"sure {hold*5:3d}dk | {n/52:5.0f}/hafta (long {len(L)/52:.0f} short {len(S_)/52:.0f}) | karli %{w:3.0f} | "
                      f"ort %{a:+.3f} | PF {pf:.2f} (long {pf_l:.2f} short {pf_s:.2f}) | ilk 6 ay PF {stats(h1,0.001)[3]:.2f} "
                      f"son 6 ay PF {stats(h2,0.001)[3]:.2f} | 100$ -> {portfolio(trades,0.001):,.0f}$ "
                      f"(limit emirle {portfolio(trades,0.0006):,.0f}$)", flush=True)
    print(f"BITTI ({round(time.time()-t0)} sn)")
