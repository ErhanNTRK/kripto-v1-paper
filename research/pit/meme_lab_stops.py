"""Follow-up to meme_lab.py (5 Oct 2026): the intraday pump short (24h +40% and a 5x volume hour) and the new-listing
short only paid without a tight stop. Here: stops +25/40/60% over the ENTRY (not the pump high), 3- and 7-day holds,
0.20% costs + 0.03%/day funding, one trade per coin per 3 days; split by year (Sep 2024-Aug 2025 / Sep 2025-Aug 2026)."""
import statistics as S, sys
sys.path.insert(0, "research/pit")
import meme_lab as L

data = L.load(); DAY, H = L.DAY, L.H
def short_path(rows, i, days, stop):
    e = rows[i + 1][1]; end = min(len(rows) - 1, i + 1 + days * 24)
    for k in range(i + 1, end + 1):
        if stop and rows[k][2] >= e * (1 + stop): return -stop - L.COST - L.FUND_DAY * (k - i) / 24
    return 1 - rows[end][4] / e - L.COST - L.FUND_DAY * days
def line(lbl, xs):
    y1 = [r for t, r in xs if t < L.YEAR2]; y2 = [r for t, r in xs if t >= L.YEAR2]
    rs = [r for _, r in xs]
    return (f"{lbl}: n={len(rs):4d} ort %{100*S.mean(rs):+5.2f} ortanca %{100*S.median(rs):+6.2f} kazanan %{100*sum(r>0 for r in rs)/len(rs):3.0f} | "
            f"1.yil ort %{100*S.mean(y1) if y1 else 0:+5.2f} (n={len(y1)}) 2.yil ort %{100*S.mean(y2) if y2 else 0:+5.2f} (n={len(y2)}) | en kotu %{100*min(rs):+.0f}")
print("=== Saatlik pompa short'u (24s +%40, hacim 5x) ===")
events = []
for s, rows in data.items():
    last = -1
    for i in range(170, len(rows) - 1):
        if i - last < 72 or rows[i][4] / rows[i - 24][4] - 1 < 0.40: continue
        avg = sum(r[5] for r in rows[i - 168:i]) / 168
        if avg <= 0 or rows[i][5] < 5 * avg: continue
        last = i; events.append((s, i))
for days in (3, 7):
    for stop in (0.25, 0.40, 0.60, None):
        xs = [(data[s][i][0], short_path(data[s], i, days, stop)) for s, i in events]
        print(line(f"  {days}g tut, stop {'yok ' if not stop else f'+%{int(100*stop)}'}", xs))
print("\n=== Yeni listelenen coin short'u (listelemeden 3 gun sonra, 30 gun) ===")
first_all = min(r[0][0] for r in data.values())
lst = [(s, 71) for s, rows in data.items() if rows[0][0] >= first_all + 14 * DAY and len(rows) > 72 + 30 * 24]
for stop in (0.30, 0.50, 1.00, None):
    xs = [(data[s][i][0], short_path(data[s], i, 30, stop)) for s, i in lst]
    print(line(f"  stop {'yok ' if not stop else f'+%{int(100*stop)}'}", xs))
