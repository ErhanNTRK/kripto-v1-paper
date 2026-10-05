"""Self-switching pump fade: trade only while the fade itself won over the last K weeks. Robustness check for the pump fade (5 Oct 2026) on the older eras: 4h SPOT bars, point-in-time top 50
(~/kripto/arastirma-verisi/pit2020: Jan 2021 - Sep 2023, incl. the 2021 alt season; pit: Sep 2023 - Aug 2026).
Same rules as pump_fade_gated.py: every Monday short the N top-50 coins that rose most over 30 days, cover a week
later or at +S; ETH/BTC 50-day EMA gate open / closed / always; 0.10% a side + 0.03% a day funding. Spot prices stand
in for futures (the same coins traded both); data are cut at symbol re-use jumps like rule_fix_test.cut."""
import json, math, statistics as S, sys
from datetime import datetime, timezone
from pathlib import Path

DAY = 86_400_000
FEE, FUNDING = 0.001, 0.0003

for era in ("pit2020", "pit"):
    P = Path.home() / "kripto" / "arastirma-verisi" / era
    raw = json.loads((P / "data_4h.json").read_text(encoding="utf-8"))
    top = {int(k): v for k, v in json.loads((P / "daily_top50.json").read_text()).items()}
    close, high = {}, {}
    for s, rows in raw.items():
        c, h, last = {}, {}, None
        for r in rows:
            if last and (r["o"] / last > 3 or r["o"] / last < 1 / 3): break   # symbol re-use jump
            d = r["t"] // DAY
            c[d] = r["c"]; h[d] = max(h.get(d, r["h"]), r["h"]); last = r["c"]
        close[s], high[s] = c, h
    del raw
    days = sorted(close["BTCUSDT"])
    rd = [d for d in days if d in close.get("ETHUSDT", {})]
    ratio = [close["ETHUSDT"][d] / close["BTCUSDT"][d] for d in rd]
    ema, e = [], None
    for x in ratio: e = x if e is None else e + (x - e) * 2 / 51; ema.append(e)
    gate = {d: r > m for d, r, m in zip(rd, ratio, ema)}
    t0 = min(top) // DAY
    mondays = [d for d in days if d >= t0 + 31 and d + 6 <= days[-1]
               and datetime.fromtimestamp(d * DAY / 1000, timezone.utc).weekday() == 0]
    print(f"\n##### {era}: {datetime.fromtimestamp(mondays[0]*DAY/1000, timezone.utc):%Y-%m-%d} - "
          f"{datetime.fromtimestamp(mondays[-1]*DAY/1000, timezone.utc):%Y-%m-%d}, {len(mondays)} hafta, "
          f"kapi acik {sum(1 for d in mondays if gate.get(d - 1))} hafta")
    # every Monday's raw fade result (the trade the rule would make), so a switch can look back at it
    def week(d, N, stop):
        prev, end = d - 1, d + 6
        u = [s for s in top.get(prev * DAY, top.get(d * DAY, [])) if s != "BTCUSDT"
             and prev in close.get(s, {}) and prev - 30 in close[s] and end in close[s]]
        if len(u) < 15: return None
        picks = sorted(u, key=lambda s: close[s][prev] / close[s][prev - 30], reverse=True)[:N]
        rets = []
        for s in picks:
            e0 = close[s][prev]; r = 1 - close[s][end] / e0
            if any(high[s].get(k, 0) >= e0 * (1 + stop) for k in range(d, end + 1)): r = -stop
            rets.append(r)
        return S.mean(rets) - 2 * FEE - FUNDING * 7
    for N, stop in ((5, 0.25), (5, 0.40), (10, 0.25), (10, 0.40)):
        raw_w = {d: week(d, N, stop) for d in mondays}
        for K in (4, 8, 12):
            for use_gate in (True, False):
                eq, peak, mdd, n, wins, yearly = 1.0, 1.0, 0.0, 0, 0, {}
                for i, d in enumerate(mondays):
                    past = [raw_w[x] for x in mondays[max(0, i - K):i] if raw_w[x] is not None]
                    # the result of week i-1 is only known at its end = this Monday: no look-ahead
                    if len(past) < K or sum(past) <= 0 or raw_w[d] is None: continue
                    if use_gate and not gate.get(d - 1): continue
                    wk = raw_w[d]; eq *= 1 + wk; peak = max(peak, eq); mdd = max(mdd, 1 - eq / peak)
                    n += 1; wins += wk > 0
                    y = datetime.fromtimestamp(d * DAY / 1000, timezone.utc).year
                    yearly[y] = yearly.get(y, 1.0) * (1 + wk)
                ys = " ".join(f"{y}:x{v:.2f}" for y, v in sorted(yearly.items()))
                print(f"  son {K:2d} hafta kazandiysa{' + kapi acik' if use_gate else '            '} | ilk {N:2d} stop +%{int(100*stop)}: "
                      f"100$ -> {100*eq:5.0f} | {n:3d} hafta kazanan %{100*wins/max(1,n):3.0f} | en derin dusus %{100*mdd:.0f} | {ys}")
