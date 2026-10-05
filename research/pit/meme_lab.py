"""Memecoin / volatile-coin lab (user, 5 Oct 2026: "how do we make money from memecoins and very volatile coins? think of
everything, not only leverage"). Event studies and simple books on 1h USD-M futures bars (~/kripto/arastirma-verisi/fut1h,
Sep 2024 - Aug 2026, ~330 coins incl. ~50 memecoins) plus real funding for the memes (meme_funding.json).
Survivorship: coins delisted before today are mostly missing, which flatters longs and understates shorts.
Costs: 0.20% per round trip (fees + slippage); shorts/longs also pay 0.03%/day funding unless real funding is used.

A  listing drift      coins whose first bar is after 15 Sep 2024: from 3 days after listing, return to +7/+30/+90 days
B  funding extremes   memes: funding >= +0.10%/8h with 72h return >= +30% (overheated) / funding <= -0.10% (crowded shorts)
C  24h reversal by liquidity  daily at 00 UTC: illiquid third -> long the 10% biggest 24h losers, short the 10% winners;
                      liquid top 20 -> the reverse (momentum); 24h hold
D  intraday pump fade 24h +40% and the hour's volume >= 5x its 7-day hourly average -> short the next hour's open;
                      returns at +24h/+3d/+7d, and with a stop at the pump high
E  meme basket        all memes held equally a week at a time while BTC is above its 50-day EMA vs BTC itself"""
import json, math, statistics as S
from datetime import datetime, timezone
from pathlib import Path

BASE = Path.home() / "kripto" / "arastirma-verisi"
H, DAY = 3_600_000, 86_400_000
COST, FUND_DAY = 0.002, 0.0003
STABLE = {"USDC", "FDUSD", "TUSD", "USDP", "DAI", "USDE", "USD1", "BTCDOM", "XAUT", "PAXG", "DEFI"}
MEMES = {"DOGE", "WIF", "PENGU", "TRUMP", "BOME", "PNUT", "NEIRO", "TURBO", "MEME", "MUBARAK", "TUT", "PUMP", "ACT",
         "AIXBT", "DOGS", "NOT", "HMSTR", "PEOPLE", "1000SATS", "BANANAS31", "BROCCOLI714", "1000PEPE", "1000BONK",
         "1000SHIB", "1000FLOKI", "POPCAT", "FARTCOIN", "SPX", "MOODENG", "GOAT", "1000CAT", "MEW", "BRETT", "PONKE",
         "CHILLGUY", "MELANIA", "1MBABYDOGE", "PIPPIN", "ZEREBRO", "BAN", "1000RATS", "MYRO", "1000CHEEMS", "KOMA",
         "1000WHY", "1000X", "GRIFFAIN", "AI16Z", "VIRTUAL"}
YEAR2 = 1_756_684_800_000   # 1 Sep 2025


def load():
    data = {}
    for f in sorted((BASE / "fut1h").glob("*.json")):
        if f.stem[:-4] in STABLE: continue
        rows = json.loads(f.read_text())
        if len(rows) > 24 * 20: data[f.stem] = rows
    return data


def summ(xs, label):
    if not xs: return f"{label}: yok"
    return (f"{label}: n={len(xs):4d} | ortalama %{100 * S.mean(xs):+6.2f} | ortanca %{100 * S.median(xs):+6.2f} | "
            f"pozitif %{100 * sum(x > 0 for x in xs) / len(xs):3.0f}")


def main():
    data = load()
    meme = {s for s in data if s[:-4] in MEMES}
    print(f"{len(data)} coin, {len(meme)} memecoin\n")
    idx = {s: {r[0]: i for i, r in enumerate(rows)} for s, rows in data.items()}
    first_all = min(r[0][0] for r in data.values())

    # ---------- A. listing drift ----------
    print("=== A. Yeni listelenen coinler: listelemeden 3 gun sonra girilseydi ===")
    for grp_name, grp in (("tum yeni", None), ("memecoin", meme)):
        out = {7: [], 30: [], 90: []}
        for s, rows in data.items():
            if rows[0][0] < first_all + 14 * DAY or (grp is not None and s not in grp): continue
            i0 = 72
            if len(rows) <= i0: continue
            e = rows[i0][1]
            for d in out:
                j = i0 + d * 24
                if j < len(rows): out[d].append(rows[j][4] / e - 1)
        for d, xs in out.items():
            print("  " + summ(xs, f"{grp_name:9} {d:2d} gun sonra (LONG getirisi)"))
        shorts = [-(x) - COST - FUND_DAY * 30 for x in out[30]]
        print("  " + summ(shorts, f"{grp_name:9} 30 gun SHORT, masraf dahil"))
    print()

    # ---------- B. funding extremes ----------
    fund = json.loads((BASE / "meme_funding.json").read_text())
    print("=== B. Memecoin fonlama uclari ===")
    hot, cold = {1: [], 3: [], 7: []}, {1: [], 3: [], 7: []}
    for s, rates in fund.items():
        if s not in data: continue
        rows, ix = data[s], idx[s]
        last = -10 ** 18
        for t, r in rates:
            hour = t // H * H
            i = ix.get(hour)
            if i is None or i < 72 or hour - last < 3 * DAY: continue
            ret72 = rows[i][4] / rows[i - 72][4] - 1
            bucket = hot if (r >= 0.001 and ret72 >= 0.30) else cold if r <= -0.001 else None
            if bucket is None: continue
            last = hour
            for d in bucket:
                j = i + 1 + d * 24
                if j < len(rows): bucket[d].append(rows[j][4] / rows[i + 1][1] - 1)
    for d in (1, 3, 7):
        print("  " + summ(hot[d], f"asiri isinmis long (fon>=%0.10, 72s>=+%30) {d}g sonra fiyat"))
    for d in (1, 3, 7):
        print("  " + summ(cold[d], f"kalabalik short (fon<=-%0.10)            {d}g sonra fiyat"))
    print()

    # ---------- C. 24h reversal by liquidity ----------
    print("=== C. Gunluk tersine donus / momentum (likiditeye gore) ===")
    days = sorted({r[0] // DAY * DAY for r in data["BTCUSDT"]})
    books = {"likit olmayan: dusenleri al, yukselenleri sat": [], "likit ilk 20: yukselenleri al, dusenleri sat": []}
    for dday in days[8:-2]:
        rows_now = []
        for s, rows in data.items():
            i = idx[s].get(dday)
            if i is None or i < 24 * 7 or i + 24 >= len(rows): continue
            r24 = rows[i][1] / rows[i - 24][1] - 1
            fwd = rows[i + 24][1] / rows[i][1] - 1
            vol7 = sum(r[5] for r in rows[i - 168:i])
            rows_now.append((vol7, r24, fwd, dday))
        if len(rows_now) < 60: continue
        rows_now.sort(reverse=True)
        liquid, illiquid = rows_now[:20], rows_now[-len(rows_now) // 3:]
        k = max(2, len(illiquid) // 10)
        il = sorted(illiquid, key=lambda x: x[1])
        books["likit olmayan: dusenleri al, yukselenleri sat"].append(
            (dday, 0.5 * S.mean(x[2] for x in il[:k]) - 0.5 * S.mean(x[2] for x in il[-k:]) - COST))
        lq = sorted(liquid, key=lambda x: x[1])
        books["likit ilk 20: yukselenleri al, dusenleri sat"].append(
            (dday, 0.5 * S.mean(x[2] for x in lq[-3:]) - 0.5 * S.mean(x[2] for x in lq[:3]) - COST))
    for name, xs in books.items():
        eq = math.prod(1 + r for _, r in xs)
        y1 = math.prod(1 + r for d, r in xs if d < YEAR2); y2 = math.prod(1 + r for d, r in xs if d >= YEAR2)
        print(f"  {name}: {len(xs)} gun | gun basi net %{100 * S.mean(r for _, r in xs):+.3f} | 100$ -> {100 * eq:.0f} | 1.yil x{y1:.2f} 2.yil x{y2:.2f}")
    print()

    # ---------- D. intraday pump fade ----------
    print("=== D. Saatlik pompa (24s +%40 ve saatlik hacim 7 gunluk ortalamanin 5 kati) sonrasi ===")
    for grp_name, grp in (("tum coinler", None), ("memecoin", meme)):
        out = {1: [], 3: [], 7: []}; stopped = {3: []}
        for s, rows in data.items():
            if grp is not None and s not in grp: continue
            last = -1
            for i in range(170, len(rows) - 1):
                if i - last < 72: continue
                if rows[i][4] / rows[i - 24][4] - 1 < 0.40: continue
                avg = sum(r[5] for r in rows[i - 168:i]) / 168
                if avg <= 0 or rows[i][5] < 5 * avg: continue
                last = i
                e = rows[i + 1][1]; high = max(r[2] for r in rows[i - 24:i + 1])
                for d in out:
                    j = i + 1 + d * 24
                    if j < len(rows): out[d].append(1 - rows[j][4] / e - COST - FUND_DAY * d)
                # 3-day short with a stop 5% above the pump high
                j_end = min(len(rows) - 1, i + 1 + 72); r = 1 - rows[j_end][4] / e
                for k in range(i + 1, j_end + 1):
                    if rows[k][2] >= high * 1.05: r = 1 - high * 1.05 / e; break
                stopped[3].append(r - COST - FUND_DAY * 3)
        for d, xs in out.items():
            print("  " + summ(xs, f"{grp_name:11} SHORT {d}g tut (stopsuz, net)"))
        print("  " + summ(stopped[3], f"{grp_name:11} SHORT 3g, stop zirve+%5 (net)"))
    print()

    # ---------- E. meme basket vs BTC ----------
    print("=== E. Memecoin sepeti (esit agirlik, haftalik) BTC 50 gunluk ortalamanin ustundeyken ===")
    btc = data["BTCUSDT"]
    daily = {r[0] // DAY: r[4] for r in btc}
    dk = sorted(daily); ema, e = {}, None
    for d in dk: e = daily[d] if e is None else e + (daily[d] - e) * 2 / 51; ema[d] = e
    eq_m, eq_b, eq_ma, n = 1.0, 1.0, 1.0, 0
    for d in dk[60:-8]:
        if datetime.fromtimestamp(d * DAY / 1000, timezone.utc).weekday() != 0: continue
        t0, t1 = d * DAY, (d + 7) * DAY
        rets = [data[s][idx[s][t1]][1] / data[s][idx[s][t0]][1] - 1 for s in meme if t0 in idx[s] and t1 in idx[s]]
        if len(rets) < 8: continue
        mret = S.mean(rets) - COST * 0.3 - FUND_DAY * 7
        bret = daily[d + 7] / daily[d] - 1 if d + 7 in daily else 0
        eq_ma *= 1 + mret
        if daily[d - 1] > ema[d - 1]: eq_m *= 1 + mret; eq_b *= 1 + bret; n += 1
    print(f"  BTC>EMA50 haftalarinda ({n} hafta): meme sepeti 100$ -> {100 * eq_m:.0f} | BTC 100$ -> {100 * eq_b:.0f}")
    print(f"  Her hafta meme sepeti (filtresiz): 100$ -> {100 * eq_ma:.0f}")


if __name__ == "__main__":
    main()
