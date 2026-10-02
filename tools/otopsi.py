"""Trade autopsy (user, 2 Oct 2026): why did each real trade -- the bot's and the user's own -- win or lose?
Reads the bot's ledger (~/kripto/islem-kayitlari/islemler.csv, from Binance fills) and public futures prices only.
No keys, no orders.

  python tools/otopsi.py            all closed trades
  python tools/otopsi.py 2026-09-28 only trades closed on or after that day

Per trade, on 5m bars: the best and worst move while open (MFE / MAE), and the best move in the 24 hours after
the exit. On 2H bars before the entry: the 24h and 7-day run-up and the distance above EMA20 in ATRs.
Tags (one trade can carry several):
  pompadan sonra   24h move into the entry > 15% or 7-day > 30% (the NIL/MUBARAK type)
  uzamis giris     entry more than 2 ATR(2H) beyond EMA20 in the trade's direction
  hic acilmadi     never more than +0.5% in favour
  kari geri verdi  was +2% or more in favour, closed at a loss
  stop avi         closed at a loss, and within 24h price came back past the entry
  erken cikti      after the exit price went another 3%+ in the trade's direction within 24h
Writes otopsi.csv and otopsi.txt next to the ledger.
"""
import csv, json, sys, time, urllib.request
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

DIR = Path.home() / "kripto" / "islem-kayitlari"
API = "https://fapi.binance.com/fapi/v1/klines"
TR = timezone(timedelta(hours=3))
M5, H2, DAY = 300_000, 7_200_000, 86_400_000


def klines(symbol, interval, start, end):
    out = []
    while start < end:
        url = f"{API}?symbol={symbol}&interval={interval}&startTime={start}&endTime={end - 1}&limit=1500"
        with urllib.request.urlopen(url, timeout=20) as r:
            batch = json.loads(r.read())
        if not batch: break
        out += [dict(t=int(b[0]), h=float(b[2]), l=float(b[3]), c=float(b[4])) for b in batch]
        start = int(batch[-1][0]) + 1
        if len(batch) < 1500: break
    return out


def ms(text):
    return int(datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=TR).timestamp() * 1000)


def context(symbol, entry_t, long):
    """Run-up and stretch on the 2H bars that had closed before the entry."""
    bars = [b for b in klines(symbol, "2h", entry_t - 120 * H2, entry_t) if b["t"] + H2 <= entry_t]
    if len(bars) < 30: return None, None, None
    c = [b["c"] for b in bars]
    ema = c[0]
    for x in c[1:]: ema += (x - ema) * 2 / 21
    tr = [max(b["h"], p["c"]) - min(b["l"], p["c"]) for p, b in zip(bars, bars[1:])]
    atr = sum(tr[-14:]) / 14
    run24 = c[-1] / c[-13] - 1
    run7 = c[-1] / c[-85] - 1 if len(c) > 85 else None
    ext = (c[-1] - ema) / atr if atr else None
    sign = 1 if long else -1
    return sign * run24, (sign * run7 if run7 is not None else None), (sign * ext if ext is not None else None)


def autopsy(row):
    long = row["yon"] == "long"
    sign = 1 if long else -1
    t0, t1 = ms(row["acilis"]), ms(row["kapanis"])
    entry, exit_ = float(row["giris_fiyati"]), float(row["cikis_fiyati"])
    now = int(time.time() * 1000)
    bars = klines(row["coin"], "5m", t0 // M5 * M5, min(now, t1 + DAY))
    held = [b for b in bars if b["t"] < t1] or bars[:1]
    after = [b for b in bars if b["t"] >= t1]
    fav = lambda b: sign * ((b["h"] if long else b["l"]) / entry - 1)
    adv = lambda b: sign * ((b["l"] if long else b["h"]) / entry - 1)
    mfe = max(map(fav, held)) if held else 0.0
    mae = min(map(adv, held)) if held else 0.0
    after_best = max((sign * ((b["h"] if long else b["l"]) / exit_ - 1) for b in after), default=None)
    back_to_entry = any((b["h"] >= entry) if long else (b["l"] <= entry) for b in after)
    run24, run7, ext = context(row["coin"], t0, long)
    net = float(row["net_kz_usdt"])
    tags = []
    if (run24 is not None and run24 > 0.15) or (run7 is not None and run7 > 0.30): tags.append("pompadan sonra")
    if ext is not None and ext > 2: tags.append("uzamis giris")
    if mfe < 0.005: tags.append("hic acilmadi")
    if mfe >= 0.02 and net < 0: tags.append("kari geri verdi")
    if net < 0 and back_to_entry and after: tags.append("stop avi")
    if after_best is not None and after_best >= 0.03: tags.append("erken cikti")
    return dict(row, mfe=round(100 * mfe, 2), mae=round(100 * mae, 2),
                sonra_en_iyi=None if after_best is None else round(100 * after_best, 2),
                giris_oncesi_24s=None if run24 is None else round(100 * run24, 1),
                giris_oncesi_7g=None if run7 is None else round(100 * run7, 1),
                ema20_uzaklik_atr=None if ext is None else round(ext, 2),
                etiketler=", ".join(tags) or "temiz")


def summary(rows):
    out = []
    by_sys = defaultdict(list)
    for r in rows: by_sys[r["sistem"]].append(r)
    for sys_name, rs in sorted(by_sys.items(), key=lambda x: -len(x[1])):
        net = sum(float(r["net_kz_usdt"]) for r in rs)
        wins = sum(float(r["net_kz_usdt"]) > 0 for r in rs)
        reached2 = sum(r["mfe"] >= 2 for r in rs)
        out.append(f"\n{sys_name}: {len(rs)} islem, {wins} kazanan, net {net:+.2f} USDT | "
                   f"+%2'ye ulasan {reached2} | ort. en iyi an %{sum(r['mfe'] for r in rs) / len(rs):.1f}, "
                   f"ort. en kotu an %{sum(r['mae'] for r in rs) / len(rs):.1f}")
        tags = defaultdict(lambda: [0, 0.0])
        for r in rs:
            for t in r["etiketler"].split(", "):
                tags[t][0] += 1; tags[t][1] += float(r["net_kz_usdt"])
        for t, (n, s) in sorted(tags.items(), key=lambda x: x[1][1]):
            out.append(f"   {t:16} {n:3d} islem  net {s:+7.2f} USDT")
    worst = sorted(rows, key=lambda r: float(r["net_kz_usdt"]))[:8]
    out.append("\nEn cok kaybettiren 8 islem:")
    for r in worst:
        out.append(f"   {r['acilis'][5:]} {r['coin']:12} {r['sistem']:5} {r['yon']:5} net {float(r['net_kz_usdt']):+.2f} | "
                   f"en iyi %{r['mfe']:+.1f} en kotu %{r['mae']:+.1f} | once 24s %{r['giris_oncesi_24s']} 7g %{r['giris_oncesi_7g']} "
                   f"| {r['kapanis_turu']} | {r['etiketler']}")
    return out


def main():
    since = sys.argv[1] if len(sys.argv) > 1 else "0000"
    with open(DIR / "islemler.csv", encoding="utf-8-sig") as f:
        rows = [r for r in csv.DictReader(f, delimiter=";") if r["kapanis"] >= since]
    done = []
    for r in rows:
        try:
            done.append(autopsy(r))
        except Exception as e:  # one bad symbol (delisted, renamed) must not stop the rest
            print(f"{r['coin']} {r['acilis']}: atlandi ({e})", file=sys.stderr)
    with open(DIR / "otopsi.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(done[0]), delimiter=";"); w.writeheader(); w.writerows(done)
    text = "\n".join([f"Islem otopsisi -- {datetime.now(TR):%Y-%m-%d %H:%M} | {len(done)} kapanmis islem"] + summary(done))
    (DIR / "otopsi.txt").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
