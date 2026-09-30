"""SHORT cups on 2H and 4H bars with quick exits (user, 30 Sep 2026: "20 days is too long; find shorter cups and
nibble"). Point-in-time top-50, both eras (PIT_DIR), costs 0.07%/side, data cut at symbol re-use jumps.
  rim P    : a close that is the highest close of the 60 bars before the breakout (5 days on 2H, 10 days on 4H)
  cup      : the low after the rim falls DEPTH under P (variants 5-15% and 8-25%), trough >= 3 bars after the rim,
             breakout >= 3 bars after the trough, rim-to-breakout 8-60 bars, no close above P in between
  breakout : first close above P; buy at the next bar's open. Control: any first close above the 60-bar high.
  filter   : none, or BTC above its EMA50 on the same timeframe
Exits (stop 2 ATR, max hold 24h or 48h): take-profit +2% / +3% / +5%, or no take-profit (hold to the time limit).
Per-trade stats per era, and a pot simulation for the leading variants: 100 USDT, 1.5% risk per trade against the
stop, max 10 positions, notional <= 3x, one position per coin; 12-month windows starting every month."""
import os, json, statistics as S
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
PIT = Path(os.environ["PIT_DIR"]); TAG = "2021-23" if "pit2020" in str(PIT) else "2023-26"
DAY, COST = 86_400_000, 0.0007
daily_top = {int(k): set(v) for k, v in json.loads((PIT / "daily_top50.json").read_text()).items()}
ts = lambda y, m: int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)
STARTS = ([ts(2021 + (m - 1) // 12, (m - 1) % 12 + 1) for m in range(1, 22)] if TAG == "2021-23"
          else [ts(2023 + (m - 1) // 12, (m - 1) % 12 + 1) for m in range(9, 33)])
L = 60


def cut(rows):
    keep = rows[:1]
    for a, b in zip(rows, rows[1:]):
        if a["c"] > 0 and (b["o"] / a["c"] > 3 or b["o"] / a["c"] < 1 / 3 or b["t"] - a["t"] > 3 * DAY):
            break
        keep.append(b)
    return keep


def with_atr(rows):
    atr, out = None, []
    for i, r in enumerate(rows):
        tr = r["h"] - r["l"] if i == 0 else max(r["h"] - r["l"], abs(r["h"] - rows[i - 1]["c"]), abs(r["l"] - rows[i - 1]["c"]))
        atr = tr if atr is None else atr + (tr - atr) / 14
        out.append(atr)
    return out


def trade(rows, i, stop, tp, max_bars):
    """Enter at rows[i] open. Returns (return on notional, exit time, stop distance fraction)."""
    entry = rows[i]["o"] * (1 + COST)
    if entry <= stop:
        return None
    target = entry * (1 + tp) if tp else None
    for j in range(i, min(len(rows), i + max_bars)):
        r = rows[j]
        if r["l"] <= stop:
            return ((min(r["o"], stop) if j > i else stop) * (1 - COST) / entry - 1, r["t"], 1 - stop / entry)
        if target and r["h"] >= target and j > i:
            return (max(r["o"], target) * (1 - COST) / entry - 1, r["t"], 1 - stop / entry)
    last = rows[min(len(rows), i + max_bars) - 1]
    return (last["c"] * (1 - COST) / entry - 1, last["t"], 1 - stop / entry)


def pot(trades, s0, s1, risk=0.015, maxpos=10, lev=3):
    """trades: (entry_t, exit_t, symbol, ret, stopfrac), chronological. Equity booked at exits."""
    eq, open_, events = 100.0, {}, []
    for et, xt, s, r, sf in trades:
        if not (s0 <= et < s1):
            continue
        events.append((et, 1, s, r, sf, xt))
    events.sort()
    queue = []
    for et, _, s, r, sf, xt in events:
        for q in sorted(list(open_.items()), key=lambda kv: kv[1][0]):
            if q[1][0] <= et:
                eq += q[1][1]; del open_[q[0]]
        if s in open_ or len(open_) >= maxpos:
            continue
        notional = min(eq * risk / max(sf, 1e-6), lev * eq - sum(v[2] for v in open_.values()))
        if notional < 5:
            continue
        open_[s] = (xt, notional * r, notional)
    for k, v in open_.items():
        eq += v[1]
    return eq


for fl, bars_24h in (("2h", 12), ("4h", 6)):
    raw = json.loads((PIT / f"data_{fl}.json").read_text(encoding="utf-8"))
    btc = raw.pop("BTCUSDT")
    bema, e = {}, None
    for r in btc:
        e = r["c"] if e is None else e + (r["c"] - e) * 2 / 51
        bema[r["t"]] = r["c"] > e
    stats = defaultdict(list)          # (kind, depth, filter, exit) -> [(entry_t, exit_t, sym, ret, stopfrac)]
    for s, rows in raw.items():
        rows = cut(rows)
        if len(rows) < 200:
            continue
        atr = with_atr(rows)
        closes = [r["c"] for r in rows]
        dq = deque()                   # indices of the rolling 60-bar closing max (monotonic)
        for i in range(len(rows) - 1):
            while dq and dq[0] < i - L:
                dq.popleft()
            if len(dq) and i >= L:
                p = dq[0]; P = closes[p]
                d = rows[i]["t"] // DAY * DAY
                if closes[i] > P and closes[i - 1] <= P and s in daily_top.get(d, ()):
                    kinds = [("kontrol", None)]
                    if i - p >= 8 and i - p <= 60:
                        tq = min(range(p + 1, i), key=lambda k: rows[k]["l"])
                        depth = 1 - rows[tq]["l"] / P
                        if tq - p >= 3 and i - tq >= 3:
                            if 0.05 <= depth <= 0.15:
                                kinds.append(("KISA CANAK", "%5-15"))
                            if 0.08 <= depth <= 0.25:
                                kinds.append(("KISA CANAK", "%8-25"))
                    filt = bema.get(rows[i]["t"], False)
                    o = rows[i + 1]["o"]; stop = o - 2 * atr[i]
                    for tp in (0.02, 0.03, 0.05, None):
                        for hold in (bars_24h, 2 * bars_24h):
                            res = trade(rows, i + 1, stop, tp, hold)
                            if res is None:
                                continue
                            ex = f"{'+%' + str(int(tp * 100)) if tp else 'hedefsiz'} {hold * (24 // bars_24h)}s"
                            for kind, depth in kinds:
                                key = (kind, depth, ex)
                                stats[key + ("filtresiz",)].append((rows[i + 1]["t"], res[1], s, res[0], res[2]))
                                if filt:
                                    stats[key + ("BTC>EMA50",)].append((rows[i + 1]["t"], res[1], s, res[0], res[2]))
            while dq and closes[dq[-1]] <= closes[i]:
                dq.pop()
            dq.append(i)
    del raw
    weeks = (max(STARTS) + 365 * DAY - min(STARTS)) / (7 * DAY)
    print(f"\n=== {TAG} {fl.upper()} ===", flush=True)
    rows_out = []
    for k, tr in stats.items():
        rs = [x[3] for x in tr]
        w = sum(x for x in rs if x > 0); l = -sum(x for x in rs if x <= 0)
        rows_out.append((k, len(rs), sum(x > 0 for x in rs) / len(rs), S.mean(rs), w / l if l else 9.99, tr))
    for k, n, win, avg, pf, tr in sorted(rows_out, key=lambda x: (x[0][0] != "KISA CANAK", -x[4]))[:24]:
        ends = sorted(pot(sorted(tr), s0, s0 + 365 * DAY) for s0 in STARTS)
        print(f"  {k[0]:10} {str(k[1] or ''):6} {k[2]:12} {k[3]:10} | n {n:5d} ({n/weeks:4.1f}/hf) kaz %{100*win:3.0f} "
              f"ort {100*avg:+5.2f}% PF {pf:4.2f} | 100$ 12 ay: tipik {S.median(ends):5.0f}$ en kotu {ends[0]:5.0f}$ "
              f"en iyi {ends[-1]:5.0f}$", flush=True)
print("BITTI")
