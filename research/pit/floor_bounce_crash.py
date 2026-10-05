"""Follow-up to floor_bounce.py (5 Oct 2026): only floors reached after a >=30% fall within ~2 days (the user's
龙虾 case). Lists where the trades cluster in time. Result: 66 trades (30-day floor), +4.5%/trade, but 27 of them on
10 and 20 Dec 2024 (market-wide flash crashes); without those ~+0.8%/trade, and the second year -0.7%/trade."""
import sys, statistics as S, random
from collections import Counter, defaultdict
from datetime import datetime, timezone
sys.path.insert(0, "research/pit")
import floor_bounce as fb
data = fb.load(); rng = random.Random(7)
def crash_filter(rows, sigs, drop):
    return [(i, F) for i, F in sigs if min(r[3] for r in rows[i - 24:i + 1]) <= max(r[2] for r in rows[i - 48:i - 6]) * (1 - drop)]
for days in (30, 60):
    base = {s: fb.signals(r, days, 0.25) for s, r in data.items()}
    sigs = {s: crash_filter(data[s], v, 0.30) for s, v in base.items()}
    ts, _ = fb.run_variant(data, sigs, 0.01, 0.10, rng)
    day = lambda t: datetime.fromtimestamp(t["t"] / 1000, timezone.utc).strftime("%Y-%m-%d")
    byday = defaultdict(list)
    for t in ts: byday[day(t)].append(t["ret"])
    print(f"\nzemin {days}g, cokus>=%30, stop zemin-%1, hedef +%10: {len(ts)} islem, {len(byday)} farkli gun")
    for d, r in sorted(byday.items(), key=lambda x: -len(x[1]))[:6]:
        print(f"   {d}: {len(r)} islem, ort %{100*S.mean(r):+.1f}")
    rest = [t["ret"] for t in ts if day(t) not in ("2025-10-10", "2025-10-11")]
    print(f"   10-11 Ekim 2025 cokusu haric: {len(rest)} islem, kazanan %{100*sum(x>0 for x in rest)/max(1,len(rest)):.0f}, ort %{100*S.mean(rest) if rest else 0:+.2f}")
    y1 = [t["ret"] for t in ts if t["t"] < fb.SPLIT]; y2 = [t["ret"] for t in ts if t["t"] >= fb.SPLIT]
    print(f"   1.yil {len(y1)} islem ort %{100*S.mean(y1) if y1 else 0:+.2f} | 2.yil {len(y2)} islem ort %{100*S.mean(y2) if y2 else 0:+.2f}")
    m = Counter(day(t)[:7] for t in ts); print("   aylara gore:", dict(sorted(m.items())))
