"""Turn the failed rotation around (5 Oct 2026): buying last month's strongest coins for a week lost almost
everything (rotation.py: 100$ -> 0-29$, 24-37% winning weeks), so SHORT them instead. Also the pair the same test
pointed at: over the gated weeks BTC held made +27% while the whole top 100 held lost 62%.

1h USD-M futures bars (~/kripto/arastirma-verisi/fut1h, Sep 2024 - Aug 2026) read as daily bars (UTC).
  fade    every Monday 00:00 UTC short the N coins of the top 100 (30-day volume) that rose most over the last L days;
          cover the next Monday, or at the stop (+S over the entry, on the daily highs, filled at the stop)
  pair    every Monday long BTC with half the money and short the whole top 100 equally with the other half
  costs   0.10% per traded side; funding for a short in a pumped coin can run either way, so two cases:
          0.03% a day paid (normal) and 0.10% a day paid (crowded shorts)
  margin  1x: a week's result is the plain price move (a 2x / 3x book multiplies it)"""
import json, math, statistics as S
from datetime import datetime, timezone
from pathlib import Path

DIR = Path.home() / "kripto" / "arastirma-verisi" / "fut1h"
DAY = 86_400_000
STABLE = {"USDC", "FDUSD", "TUSD", "USDP", "DAI", "USDE", "USD1", "BTCDOM", "XAUT", "PAXG", "DEFI"}
FEE = 0.001


def load():
    close, high, vol = {}, {}, {}
    for f in sorted(DIR.glob("*.json")):
        if f.stem[:-4] in STABLE: continue
        c, h, v = {}, {}, {}
        for t, o, hi, lo, cl, qv in json.loads(f.read_text()):
            d = t // DAY
            c[d] = cl; h[d] = max(h.get(d, hi), hi); v[d] = v.get(d, 0.0) + qv
        close[f.stem], high[f.stem], vol[f.stem] = c, h, v
    return close, high, vol


def universe(close, vol, prev, end, L, top=100):
    u = [(sum(vol[s].get(prev - k, 0.0) for k in range(30)), s) for s in close
         if prev in close[s] and prev - L in close[s] and end in close[s]]
    return [s for _, s in sorted(u, reverse=True)[:top]]


def dd(curve):
    peak, worst = curve[0], 0.0
    for x in curve:
        peak = max(peak, x); worst = max(worst, 1 - x / peak)
    return worst


def main():
    close, high, vol = load()
    days = sorted(close["BTCUSDT"])
    mondays = [d for d in days if d >= days[0] + 70 and d + 6 <= days[-1]
               and datetime.fromtimestamp(d * DAY / 1000, timezone.utc).weekday() == 0]
    split = int(datetime(2025, 9, 1, tzinfo=timezone.utc).timestamp() * 1000 // DAY)
    print(f"{len(mondays)} hafta, {datetime.fromtimestamp(mondays[0]*DAY/1000, timezone.utc):%Y-%m-%d} - "
          f"{datetime.fromtimestamp(mondays[-1]*DAY/1000, timezone.utc):%Y-%m-%d}; kaldiracsiz (1x)\n")
    for funding in (0.0003, 0.001):
        print(f"=== short fonlama maliyeti gunde %{100 * funding:.2f} ===")
        for L in (14, 30):
            for N in (3, 5, 10):
                for stop in (None, 0.25, 0.40):
                    eq, curve, weeks, worst_week = 1.0, [1.0], [], 0.0
                    for d in mondays:
                        prev, end = d - 1, d + 6
                        u = universe(close, vol, prev, end, L)
                        picks = sorted(u, key=lambda s: close[s][prev] / close[s][prev - L], reverse=True)[:N]
                        rets = []
                        for s in picks:
                            e = close[s][prev]
                            r = 1 - close[s][end] / e                      # short: gains when price falls
                            if stop and any(high[s].get(k, 0) >= e * (1 + stop) for k in range(d, end + 1)):
                                r = -stop
                            rets.append(r)
                        wk = S.mean(rets) - 2 * FEE - funding * 7
                        eq *= 1 + wk; curve.append(eq); weeks.append((d, wk)); worst_week = min(worst_week, wk)
                    y1 = math.prod(1 + w for d, w in weeks if d < split); y2 = math.prod(1 + w for d, w in weeks if d >= split)
                    win = sum(w > 0 for _, w in weeks) / len(weeks)
                    print(f"  son {L}g en cok yukselen {N:2d} short, stop {'yok ' if not stop else f'+%{int(100*stop)}'}: "
                          f"100$ -> {100 * eq:7.0f} | 1.yil x{y1:5.2f} 2.yil x{y2:5.2f} | kazanan hafta %{100 * win:3.0f} | "
                          f"en kotu hafta %{100 * worst_week:+.0f} | en derin dusus %{100 * dd(curve):.0f}")
        print()
    print("=== cift: BTC long (yarim) + ilk 100 esit short (yarim) ===")
    eq, curve, weeks = 1.0, [1.0], []
    for d in mondays:
        prev, end = d - 1, d + 6
        u = universe(close, vol, prev, end, 30)
        b = close["BTCUSDT"][end] / close["BTCUSDT"][prev] - 1
        a = S.mean(close[s][end] / close[s][prev] - 1 for s in u)
        wk = 0.5 * b - 0.5 * a - 2 * FEE - 0.0003 * 7
        eq *= 1 + wk; curve.append(eq); weeks.append((d, wk))
    y1 = math.prod(1 + w for d, w in weeks if d < split); y2 = math.prod(1 + w for d, w in weeks if d >= split)
    print(f"  100$ -> {100 * eq:.0f} | 1.yil x{y1:.2f} 2.yil x{y2:.2f} | kazanan hafta %{100 * sum(w > 0 for _, w in weeks) / len(weeks):.0f} | "
          f"en derin dusus %{100 * dd(curve):.0f}")


if __name__ == "__main__":
    main()
