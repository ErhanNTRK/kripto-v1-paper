"""The user's "floor" method (5 Oct 2026), on 1h USD-M futures bars, ~300 coins, Sep 2024 - Aug 2026
(~/kripto/arastirma-verisi/fut1h, built by fut1h_build.py).

The user's words: look at the coins in a strong fall, find the level they keep bouncing from (the lowest level of
the last 1-2 months); if price does not go under that floor, buy above it and take a quick profit; a stop too close
to the floor got shaken out (-6.11), a stop further under it would have won; waiting too long gave gains back.

Rules (decided at a 1h close, filled at the next bar's open):
  fall        the close is at least 15% under the close 7 days earlier
  floor F     the lowest low of the last W days (30 or 60), not counting the last 24h, set at least 3 days ago
  retest      the last 24h came down to within 10% of F, and never under it
  bounce      the close is at least 3% above the last 24h low, and at most Z above F (10% or 25%)
  stop        F minus s (1, 3, 5 or 10%); a trade whose stop would be more than 35% away is skipped
  exit        target +5 / +10 / +20%, or after 72 hours at the close; stop and target in one bar -> stop
  one trade per coin at a time; the same coin can come back 6 hours after an exit
Costs: 0.20% a trade (0.10% fees + 0.10% extra slippage for thin coins).
Control: the same falling coins bought at random hours (one draw a day per coin), with the same stop distance
(the variant's median) and the same exits -- does the floor add anything over "buy a falling coin"?
Money: 100$, at most 8 trades at once, each 10% of equity as margin at 3x."""
import json, random, statistics as S
from pathlib import Path

DIR = Path.home() / "kripto" / "arastirma-verisi" / "fut1h"
H = 3_600_000
SPLIT = 1_756_684_800_000  # 1 Sep 2025 UTC: first year vs second year
COST = 0.002
HOLD = 72


def load():
    data = {}
    for f in sorted(DIR.glob("*.json")):
        rows = json.loads(f.read_text())
        if len(rows) > 24 * 70: data[f.stem] = rows
    return data


def sliding_min(lows, w):
    """mins[i] = min(lows[i-w+1 .. i]) and the index where it sits (monotone deque)."""
    from collections import deque
    q, mins, idx = deque(), [None] * len(lows), [None] * len(lows)
    for i, x in enumerate(lows):
        while q and lows[q[-1]] >= x: q.pop()
        q.append(i)
        if q[0] <= i - w: q.popleft()
        mins[i], idx[i] = lows[q[0]], q[0]
    return mins, idx


def signals(rows, days, zone):
    w = days * 24
    o, h, l, c = ([r[k] for r in rows] for k in (1, 2, 3, 4))
    floor, fidx = sliding_min(l, w)
    low24, _ = sliding_min(l, 24)
    out = []
    for i in range(w + 24, len(rows) - 1):
        j = i - 24                     # floor from the bars before the last 24h
        F = floor[j]
        if not F or fidx[j] > i - 72: continue
        if c[i] > c[i - 168] * 0.85: continue
        if low24[i] < F or low24[i] > F * 1.10: continue
        if c[i] < low24[i] * 1.03 or c[i] > F * (1 + zone): continue
        out.append((i, F))
    return out


def trade(rows, i, stop, target):
    """Enter at bar i+1's open; returns (net return, bars held, stopped, exit index)."""
    e = rows[i + 1][1]
    if stop >= e: return None
    tp = e * (1 + target) if target else None
    for k in range(i + 1, min(len(rows), i + 1 + HOLD)):
        lo, hi = rows[k][3], rows[k][2]
        if lo <= stop: return min(rows[k][1], stop) / e - 1 - COST, k - i, True, k
        if tp and hi >= tp: return target - COST, k - i, False, k
    k = min(len(rows), i + 1 + HOLD) - 1
    return rows[k][4] / e - 1 - COST, k - i, False, k


CONTROL = []  # (symbol, bar) random hours in falling coins, drawn once in main()


def run_variant(data, sigs, s, target, rng):
    trades, ctrl, dists = [], [], []
    for sym, rows in data.items():
        busy = -1
        for i, F in sigs[sym]:
            if i <= busy + 6: continue
            stop = F * (1 - s)
            e = rows[i + 1][1]
            if e / stop - 1 > 0.35: continue
            r = trade(rows, i, stop, target)
            if not r: continue
            ret, held, stopped, k = r
            # after a stop: did price come back over the entry within 72h?
            back = stopped and any(rows[m][2] >= e for m in range(k + 1, min(len(rows), k + 73)))
            trades.append(dict(sym=sym, t=rows[i + 1][0], ret=ret, held=held, stopped=stopped, back=back,
                               t_exit=rows[k][0]))
            dists.append(1 - stop / e); busy = k
    if not trades: return trades, ctrl
    d = S.median(dists)
    for sym, i in CONTROL:
        rows = data[sym]
        r = trade(rows, i, rows[i + 1][1] * (1 - d), target)
        if r: ctrl.append(dict(t=rows[i + 1][0], ret=r[0], stopped=r[2]))
    return trades, ctrl


def money(trades, slots=8, margin=0.10, lev=3):
    eq, open_, worst, peak = 100.0, [], 0.0, 100.0
    for tr in sorted(trades, key=lambda x: x["t"]):
        for o in [o for o in open_ if o[0] <= tr["t"]]:
            eq += o[1]; open_.remove(o); peak = max(peak, eq); worst = max(worst, 1 - eq / peak)
        if len(open_) >= slots or eq <= 1: continue
        open_.append((tr["t_exit"], eq * margin * lev * tr["ret"]))
    for o in open_: eq += o[1]
    return eq, worst


def stats(ts):
    if not ts: return "islem yok"
    r = [t["ret"] for t in ts]
    win = sum(x > 0 for x in r) / len(r)
    gp, gl = sum(x for x in r if x > 0), -sum(x for x in r if x < 0)
    pf = gp / gl if gl else float("inf")
    return f"{len(r):5d} islem | kazanan %{100 * win:3.0f} | islem basi %{100 * S.mean(r):+5.2f} | PF {pf:4.2f}"


def main():
    data = load()
    print(f"{len(data)} coin, 1 saatlik mum; maliyet islem basi %0.20\n")
    rng = random.Random(7)
    for sym, rows in data.items():
        c = [r[4] for r in rows]
        for day in range(24 * 8, len(rows) - HOLD - 25, 24):
            i = day + rng.randrange(24)
            if c[i] <= c[i - 168] * 0.85: CONTROL.append((sym, i))
    print(f"kontrol grubu: {len(CONTROL)} rastgele saat (dusen coinlerde, coin basina gunde en fazla 1)\n")
    for days in (30, 60):
        for zone in (0.10, 0.25):
            sigs = {s: signals(r, days, zone) for s, r in data.items()}
            n = sum(map(len, sigs.values()))
            print(f"=== zemin {days} gun, giris zeminin en fazla %{int(100 * zone)} ustunde ({n} sinyal saati) ===")
            for s in (0.01, 0.03, 0.05, 0.10):
                for target in (0.05, 0.10, 0.20, None):
                    ts, ctrl = run_variant(data, sigs, s, target, rng)
                    if not ts: continue
                    y1 = [t for t in ts if t["t"] < SPLIT]; y2 = [t for t in ts if t["t"] >= SPLIT]
                    eq, dd = money(ts)
                    st = [t for t in ts if t["stopped"]]
                    back = sum(t["back"] for t in st) / len(st) if st else 0
                    tag = f"+%{int(100 * target)}" if target else "72s tut"
                    print(f" stop zemin-%{int(100 * s):<2} hedef {tag:7}: {stats(ts)} | 1.yil %{100 * S.mean(t['ret'] for t in y1) if y1 else 0:+.2f} "
                          f"2.yil %{100 * S.mean(t['ret'] for t in y2) if y2 else 0:+.2f} | stop %{100 * len(st) / len(ts):.0f} "
                          f"(sonra girise donen %{100 * back:.0f}) | 100$ -> {eq:6.0f} (en derin dusus %{100 * dd:.0f})")
                    print(f"      kontrol (ayni dusen coinler, rastgele saat): {stats(ctrl)}")
            print()


if __name__ == "__main__":
    main()
