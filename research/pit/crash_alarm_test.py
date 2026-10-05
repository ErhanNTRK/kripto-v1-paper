"""Calibrate the market-crash rebound alarm (user chose it, 5 Oct 2026) on 1h futures bars, Sep 2024 - Aug 2026.
  crash    at least S of the top 100 (30-day volume) sit X or more under their own 48h high
  rebound  within 24h of a crash hour, BTC closes 3% or more above its lowest point since the crash began
  alarm    the first rebound hour; then quiet for 72 hours
After each alarm: the equal-weight return of the 30 biggest coins and of BTC over 24h / 72h / 7 days (from the next
hour's open), against the same horizons at all hours (the baseline)."""
import json, statistics as S
from datetime import datetime, timezone
from pathlib import Path

DIR = Path.home() / "kripto" / "arastirma-verisi" / "fut1h"
H = 3_600_000
STABLE = {"USDC", "FDUSD", "TUSD", "USDP", "DAI", "USDE", "USD1", "BTCDOM", "XAUT", "PAXG", "DEFI"}

data = {}
for f in sorted(DIR.glob("*.json")):
    if f.stem[:-4] in STABLE: continue
    data[f.stem] = {r[0]: r for r in json.loads(f.read_text())}
times = sorted(data["BTCUSDT"])
btc = data["BTCUSDT"]

def topn(t, n):
    vols = []
    for s, rows in data.items():
        v = sum(rows[t - k * H][5] for k in range(0, 720, 24) if t - k * H in rows)
        if t in rows and v: vols.append((v, s))
    return [s for _, s in sorted(vols, reverse=True)[:n]]

def fwd(syms, t, hours):
    rs = []
    for s in syms:
        a, b = data[s].get(t + H), data[s].get(t + hours * H)
        if a and b: rs.append(b[4] / a[1] - 1)
    return S.mean(rs) if rs else None

samples = times[24 * 31::6]                     # every 6 hours, after a month of history
universe = {}
for t in samples[::4]: universe[t] = (topn(t, 100), topn(t, 30))   # refreshed daily
def univ(t):
    k = max(x for x in universe if x <= t); return universe[k]

for X, Sh in ((0.15, 0.30), (0.15, 0.50), (0.20, 0.30), (0.20, 0.50)):
    alarms, crash_at, last = [], None, -10 ** 18
    for t in times[24 * 31:]:
        u100, u30 = univ(t) if t >= min(universe) else (None, None)
        if not u100: continue
        if t % (2 * H) == 0 or crash_at is None:  # breadth every 2 hours (speed)
            down = 0
            for s in u100:
                r = data[s]
                hi = max((r[t - k * H][2] for k in range(48) if t - k * H in r), default=None)
                if hi and t in r and r[t][4] <= hi * (1 - X): down += 1
            if down >= Sh * len(u100): crash_at = crash_at or t
        if crash_at and t - crash_at > 24 * H: crash_at = None
        if crash_at and t - last > 72 * H:
            low = min(btc[k][3] for k in range(crash_at, t + H, H) if k in btc)
            if t in btc and btc[t][4] >= low * 1.03:
                alarms.append((t, u30)); last = t; crash_at = None
    print(f"\n=== cokus: ilk 100'un en az %{int(100 * Sh)}'i 48s zirvesinin %{int(100 * X)}+ altinda | {len(alarms)} alarm ===")
    rows = {h: [] for h in (24, 72, 168)}; brow = {h: [] for h in (24, 72, 168)}
    for t, u30 in alarms:
        r = {h: fwd(u30, t, h) for h in rows}; b = {h: fwd(["BTCUSDT"], t, h) for h in rows}
        for h in rows:
            if r[h] is not None: rows[h].append(r[h])
            if b[h] is not None: brow[h].append(b[h])
        print(f"  {datetime.fromtimestamp(t / 1000, timezone.utc):%Y-%m-%d %H:00} UTC | ilk30 24s %{100 * (r[24] or 0):+5.1f} 72s %{100 * (r[72] or 0):+5.1f} "
              f"7g %{100 * (r[168] or 0):+5.1f} | BTC 7g %{100 * (b[168] or 0):+5.1f}")
    if alarms:
        print("  ORTALAMA: " + " | ".join(f"{h}s ilk30 %{100 * S.mean(rows[h]):+.1f} (kazanan {sum(x > 0 for x in rows[h])}/{len(rows[h])}), BTC %{100 * S.mean(brow[h]):+.1f}"
                                          for h in rows if rows[h]))
base = {h: [x for x in (fwd(univ(t)[1], t, h) for t in samples[::4] if t + h * H <= times[-1]) if x is not None] for h in (24, 72, 168)}
print("\nTaban (her gun herhangi bir saat, ilk30 esit): " + " | ".join(f"{h}s %{100 * S.mean(v):+.2f}" for h, v in base.items()))
