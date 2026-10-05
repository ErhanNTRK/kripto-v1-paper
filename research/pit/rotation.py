"""Strength rotation (my proposal, chosen by the user 5 Oct 2026): every Monday buy the N strongest coins of the top
100 and hold them a week. 1h USD-M futures bars from ~/kripto/arastirma-verisi/fut1h (Sep 2024 - Aug 2026), read as
daily closes (UTC).

  universe   top 100 by the last 30 days' quote volume, stablecoins out, at least L+1 days of history
  rank       return over the last L days (14, 30 or 60), the N best (3, 5 or 10), equal weight
  entry      Monday's 00:00 UTC price (Sunday's close); exit the next Monday at the same time
  gate       ETH/BTC above its 50-day EMA (the live long gate), or no gate
  stop       none, or -15% from the entry on the daily lows (filled at the stop)
  costs      0.10% per traded side on the turnover + funding 0.03% a day (a typical long's fee)
Benchmarks over the same open weeks: BTC held, and all 100 coins of the universe held equally."""
import json, math, statistics as S
from datetime import datetime, timezone
from pathlib import Path

DIR = Path.home() / "kripto" / "arastirma-verisi" / "fut1h"
DAY = 86_400_000
STABLE = {"USDC", "FDUSD", "TUSD", "USDP", "DAI", "USDE", "USD1", "BTCDOM", "XAUT", "PAXG", "DEFI"}
FEE, FUNDING_DAY = 0.001, 0.0003


def load_daily():
    close, low, vol = {}, {}, {}
    for f in sorted(DIR.glob("*.json")):
        sym = f.stem
        if sym[:-4] in STABLE: continue
        c, l, v = {}, {}, {}
        for t, o, h, lo, cl, qv in json.loads(f.read_text()):
            d = t // DAY
            c[d] = cl                      # the last hour of the day sets the close
            l[d] = min(l.get(d, lo), lo); v[d] = v.get(d, 0.0) + qv
        close[sym], low[sym], vol[sym] = c, l, v
    return close, low, vol


def ema_series(values, n):
    out, e = [], None
    for x in values:
        e = x if e is None else e + (x - e) * 2 / (n + 1)
        out.append(e)
    return out


def run(close, low, vol, mondays, gate_ok, L, N, use_gate, stop, top=100):
    eq, curve, weeks, held = 1.0, [1.0], [], set()
    for d in mondays:
        prev = d - 1                       # Sunday's close = Monday's 00:00 entry
        end = d + 6                        # next Sunday's close = next Monday's exit
        if use_gate and not gate_ok.get(prev):
            if held: eq *= 1 - FEE * 1.0   # sell what was held
            held = set(); curve.append(eq); weeks.append(None); continue
        univ = []
        for s in close:
            c = close[s]
            if prev in c and prev - L in c and end in c:
                univ.append((sum(vol[s].get(prev - k, 0.0) for k in range(30)), s))
        univ = [s for _, s in sorted(univ, reverse=True)[:top]]
        if len(univ) < min(20, top): weeks.append(None); curve.append(eq); continue
        ranked = sorted(univ, key=lambda s: close[s][prev] / close[s][prev - L], reverse=True)[:N]
        rets = []
        for s in ranked:
            e = close[s][prev]
            r = close[s][end] / e - 1
            if stop:
                if any(low[s].get(k, e) <= e * (1 - stop) for k in range(d, end + 1)): r = -stop
            rets.append(r)
        turnover = len(set(ranked) ^ held) / max(1, N)   # share of the book that changed hands, both sides
        wk = S.mean(rets) - FEE * turnover - FUNDING_DAY * 7
        eq *= 1 + wk; held = set(ranked if not stop else ranked)
        curve.append(eq); weeks.append(wk)
    return eq, curve, weeks


def drawdown(curve):
    peak, worst = curve[0], 0.0
    for x in curve:
        peak = max(peak, x); worst = max(worst, 1 - x / peak)
    return worst


def main():
    close, low, vol = load_daily()
    days = sorted(close["BTCUSDT"])
    ratio = [close["ETHUSDT"][d] / close["BTCUSDT"][d] for d in days if d in close["ETHUSDT"]]
    rdays = [d for d in days if d in close["ETHUSDT"]]
    gate_ok = {d: r > e for d, r, e in zip(rdays, ratio, ema_series(ratio, 50))}
    first = days[0] + 70
    mondays = [d for d in days if d >= first and d + 6 <= days[-1]
               and datetime.fromtimestamp(d * DAY / 1000, timezone.utc).weekday() == 0]
    split = datetime(2025, 9, 1, tzinfo=timezone.utc).timestamp() * 1000 // DAY
    open_weeks = [d for d in mondays if gate_ok.get(d - 1)]
    print(f"{len(mondays)} hafta ({datetime.fromtimestamp(mondays[0] * DAY / 1000, timezone.utc):%Y-%m-%d} - "
          f"{datetime.fromtimestamp(mondays[-1] * DAY / 1000, timezone.utc):%Y-%m-%d}); kapi acik {len(open_weeks)} hafta\n")

    # benchmarks over the open weeks
    def hold(sym_list_fn):
        eq = 1.0
        for d in open_weeks:
            syms = sym_list_fn(d)
            rs = [close[s][d + 6] / close[s][d - 1] - 1 for s in syms if d - 1 in close[s] and d + 6 in close[s]]
            if rs: eq *= 1 + S.mean(rs) - FUNDING_DAY * 7
        return eq
    btc = hold(lambda d: ["BTCUSDT"])
    def top100(d):
        u = [(sum(vol[s].get(d - 1 - k, 0.0) for k in range(30)), s) for s in close if d - 1 in close[s] and d + 6 in close[s]]
        return [s for _, s in sorted(u, reverse=True)[:100]]
    alltop = hold(top100)
    print(f"Karsilastirma (sadece kapi acik haftalar): BTC tut 100$ -> {100 * btc:.0f} | ilk 100'un hepsi esit 100$ -> {100 * alltop:.0f}\n")

    rows = []
    for use_gate in (True, False):
        for stop in (None, 0.15):
            for L in (14, 30, 60):
                for N in (3, 5, 10):
                    eq, curve, weeks = run(close, low, vol, mondays, gate_ok, L, N, use_gate, stop)
                    w = [x for x in weeks if x is not None]
                    y1 = math.prod(1 + x for d, x in zip(mondays, weeks) if x is not None and d < split)
                    y2 = math.prod(1 + x for d, x in zip(mondays, weeks) if x is not None and d >= split)
                    rows.append((use_gate, stop, L, N, eq, y1, y2, drawdown(curve), sum(x > 0 for x in w) / max(1, len(w)), len(w)))
    for use_gate, stop, L, N, eq, y1, y2, dd, win, n in rows:
        print(f"{'kapili ' if use_gate else 'kapisiz'} {'stop-%15' if stop else 'stopsuz '} son {L:2d}g en guclu {N:2d}: "
              f"100$ -> {100 * eq:6.0f} | 1.yil x{y1:4.2f} 2.yil x{y2:4.2f} | en derin dusus %{100 * dd:3.0f} | "
              f"kazanan hafta %{100 * win:3.0f} ({n} hafta)")

    print("\n=== sadece buyuk coinler: ilk 20 / 30 / 50 (hacme gore) ===")
    for top in (20, 30, 50):
        for use_gate in (True, False):
            for L in (30, 60):
                for N in (3, 5):
                    eq, curve, weeks = run(close, low, vol, mondays, gate_ok, L, N, use_gate, None, top)
                    w = [x for x in weeks if x is not None]
                    y1 = math.prod(1 + x for d, x in zip(mondays, weeks) if x is not None and d < split)
                    y2 = math.prod(1 + x for d, x in zip(mondays, weeks) if x is not None and d >= split)
                    print(f"ilk {top:2d} {'kapili ' if use_gate else 'kapisiz'} son {L}g en guclu {N}: 100$ -> {100 * eq:5.0f} | "
                          f"1.yil x{y1:4.2f} 2.yil x{y2:4.2f} | en derin dusus %{100 * drawdown(curve):3.0f} | kazanan hafta %{100 * sum(x > 0 for x in w) / max(1, len(w)):3.0f}")


if __name__ == "__main__":
    main()
