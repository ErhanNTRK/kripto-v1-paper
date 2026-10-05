"""Intuition report card (user, 5 Oct 2026: "send it in the first message every day"). Scores the user's recorded
market calls in ~/kripto/odev/sezgiler.jsonl at 24 hours, 3 days and 7 days, on public futures prices.

  tip "dusus"     the coins will fall                     right when the price is lower
  tip "almazdim"  "I would never buy these"               right when the price is lower (buying would have lost)
  tip "tercih"    "iyi" will do better than "kotu"        right when iyi's change beats kot's

  python tools/sezgi_karnesi.py                      the card
  python tools/sezgi_karnesi.py ekle dusus "metin" BTCUSDT ETHUSDT     record a new call at today's prices
  python tools/sezgi_karnesi.py ekle tercih "metin" IYI KOTU"""
import json, sys, time, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

FILE = Path.home() / "kripto" / "odev" / "sezgiler.jsonl"
API = "https://fapi.binance.com/fapi/v1/"
H = 3_600_000
TR = timezone(timedelta(hours=3))
HORIZONS = (("24s", 24), ("3g", 72), ("7g", 168))


def get(path, **p):
    with urllib.request.urlopen(f"{API}{path}?{urllib.parse.urlencode(p)}", timeout=20) as r:
        return json.loads(r.read())


def price_at(symbol, t):
    """The close of the 1h bar that ends at or just after t; None if that hour has not closed yet."""
    k = get("klines", symbol=symbol, interval="1h", startTime=t - H, limit=2)
    now = int(time.time() * 1000)
    done = [x for x in k if int(x[6]) < now and int(x[6]) >= t - 1]
    return float(done[0][4]) if done else None


def now_price(symbol):
    return float(get("ticker/price", symbol=symbol)["price"])


def score(row, prices):
    """(right?, text) from {symbol: change since the call}."""
    if row["tip"] in ("dusus", "almazdim"):
        falls = sum(v < 0 for v in prices.values())
        txt = ", ".join(f"{s.removesuffix('USDT')} %{100 * v:+.1f}" for s, v in prices.items())
        return falls * 2 > len(prices), txt
    iyi, kotu = prices[row["iyi"]], prices[row["kotu"]]
    return iyi > kotu, (f"{row['iyi'].removesuffix('USDT')} %{100 * iyi:+.1f} vs "
                        f"{row['kotu'].removesuffix('USDT')} %{100 * kotu:+.1f}")


def card():
    rows = [json.loads(l) for l in FILE.read_text(encoding="utf-8").splitlines() if l.strip()]
    now = int(time.time() * 1000)
    lines = [f"SEZGI KARNESI -- {datetime.now(TR):%d.%m %H:%M}"]
    right = total = 0
    for row in rows:
        lines.append(f"\n{datetime.fromtimestamp(row['t'] / 1000, TR):%d.%m %H:%M}  \"{row['sezgi']}\"")
        for name, hours in HORIZONS:
            t = row["t"] + hours * H
            if t <= now:
                ch = {s: (price_at(s, t) or now_price(s)) / p - 1 for s, p in row["fiyatlar"].items()}
                ok, txt = score(row, ch)
                right += ok; total += 1
                lines.append(f"   {name:3} {'DOGRU ' if ok else 'YANLIS'} | {txt}")
            else:
                ch = {s: now_price(s) / p - 1 for s, p in row["fiyatlar"].items()}
                ok, txt = score(row, ch)
                left = (t - now) / H
                lines.append(f"   {name:3} bekliyor ({left:.0f} saat kaldi), su an {'tutuyor' if ok else 'tutmuyor'} | {txt}")
                break
    lines.append(f"\nPUAN: {right}/{total} olgunlasmis olcum dogru" + (f" (%{100 * right / total:.0f})" if total else ""))
    text = "\n".join(lines)
    (FILE.parent / "sezgi_karnesi.txt").write_text(text, encoding="utf-8")
    print(text)


def add(tip, text, symbols):
    row = {"t": int(time.time() * 1000), "tip": tip, "sezgi": text,
           "fiyatlar": {s if s.endswith("USDT") else s + "USDT": now_price(s if s.endswith("USDT") else s + "USDT")
                        for s in symbols}}
    if tip == "tercih":
        row["iyi"], row["kotu"] = list(row["fiyatlar"])[:2]
    with FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print("kaydedildi:", row)


if __name__ == "__main__":
    if len(sys.argv) > 3 and sys.argv[1] == "ekle":
        add(sys.argv[2], sys.argv[3], [s.upper() for s in sys.argv[4:]])
    else:
        card()
