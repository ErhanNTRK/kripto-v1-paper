"""pump_fade.py with the live ETH/BTC gate turned around (5 Oct 2026): the fade (short last month's biggest risers)
lost in year 1 (alt rallies) and won big in year 2 (alt bleed) -- the opposite of when the long systems work. Here the
shorts run only while ETH/BTC is BELOW its 50-day EMA (the long gate closed), else cash. Funding 0.03% a day paid."""
import math, statistics as S, sys
from datetime import datetime, timezone
sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
import pump_fade as P

close, high, vol = P.load()
days = sorted(close["BTCUSDT"])
rd = [d for d in days if d in close["ETHUSDT"]]
ratio = [close["ETHUSDT"][d] / close["BTCUSDT"][d] for d in rd]
ema, e = [], None
for x in ratio: e = x if e is None else e + (x - e) * 2 / 51; ema.append(e)
gate_open = {d: r > m for d, r, m in zip(rd, ratio, ema)}
mondays = [d for d in days if d >= days[0] + 70 and d + 6 <= days[-1]
           and datetime.fromtimestamp(d * P.DAY / 1000, timezone.utc).weekday() == 0]
split = int(datetime(2025, 9, 1, tzinfo=timezone.utc).timestamp() * 1000 // P.DAY)
closed_weeks = [d for d in mondays if not gate_open.get(d - 1)]
print(f"{len(mondays)} hafta; kapi KAPALI {len(closed_weeks)} hafta (short calisir), acik {len(mondays) - len(closed_weeks)} hafta (nakit)\n")
for mode in ("kapi kapaliyken", "her zaman", "kapi acikken"):
    for L, N, stop in ((30, 5, 0.25), (30, 5, 0.40), (30, 10, 0.25), (30, 10, 0.40), (14, 10, 0.25), (30, 5, None)):
        eq, curve, weeks, worst = 1.0, [1.0], [], 0.0
        for d in mondays:
            g = gate_open.get(d - 1)
            if (mode == "kapi kapaliyken" and g) or (mode == "kapi acikken" and not g):
                curve.append(eq); continue
            prev, end = d - 1, d + 6
            u = P.universe(close, vol, prev, end, L)
            picks = sorted(u, key=lambda s: close[s][prev] / close[s][prev - L], reverse=True)[:N]
            rets = []
            for s in picks:
                e0 = close[s][prev]; r = 1 - close[s][end] / e0
                if stop and any(high[s].get(k, 0) >= e0 * (1 + stop) for k in range(d, end + 1)): r = -stop
                rets.append(r)
            wk = S.mean(rets) - 2 * P.FEE - 0.0003 * 7
            eq *= 1 + wk; curve.append(eq); weeks.append((d, wk)); worst = min(worst, wk)
        y1 = math.prod(1 + w for d, w in weeks if d < split); y2 = math.prod(1 + w for d, w in weeks if d >= split)
        win = sum(w > 0 for _, w in weeks) / max(1, len(weeks))
        print(f"{mode:16} son {L}g ilk {N:2d} short, stop {'yok ' if not stop else f'+%{int(100*stop)}'}: 100$ -> {100*eq:6.0f} | "
              f"1.yil x{y1:4.2f} 2.yil x{y2:5.2f} | {len(weeks):2d} hafta, kazanan %{100*win:3.0f} | en kotu hafta %{100*worst:+.0f} | en derin dusus %{100*P.dd(curve):.0f}")
    print()
