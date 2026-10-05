"""Extend the 1h cache (~/kripto/arastirma-verisi/fut1h) with the big memecoins missing from the 5m zips, and fetch
8-hourly funding history for every memecoin (~/kripto/arastirma-verisi/meme_funding.json). Public futures endpoints;
existing files are kept (never re-downloaded). Sep 2024 - Aug 2026, like the rest of the cache."""
import json, time, urllib.parse, urllib.request
from pathlib import Path

BASE = Path.home() / "kripto" / "arastirma-verisi"
H = 3_600_000
START, END = 1_725_148_800_000, 1_788_220_800_000          # 2024-09-01 .. 2026-09-01 UTC
WANT = ["1000PEPEUSDT", "1000BONKUSDT", "1000SHIBUSDT", "1000FLOKIUSDT", "POPCATUSDT", "FARTCOINUSDT", "SPXUSDT",
        "MOODENGUSDT", "GOATUSDT", "1000CATUSDT", "MEWUSDT", "BRETTUSDT", "PONKEUSDT", "CHILLGUYUSDT", "MELANIAUSDT",
        "1MBABYDOGEUSDT", "PIPPINUSDT", "ZEREBROUSDT", "SUNDOGUSDT", "BANUSDT", "1000RATSUSDT", "MYROUSDT",
        "1000CHEEMSUSDT", "KOMAUSDT", "1000WHYUSDT", "1000XUSDT", "GRIFFAINUSDT", "AI16ZUSDT", "BOMEUSDT", "WIFUSDT"]
MEMES_HELD = ["DOGEUSDT", "WIFUSDT", "PENGUUSDT", "TRUMPUSDT", "BOMEUSDT", "PNUTUSDT", "NEIROUSDT", "TURBOUSDT",
              "MEMEUSDT", "MUBARAKUSDT", "TUTUSDT", "PUMPUSDT", "ACTUSDT", "AIXBTUSDT", "DOGSUSDT", "NOTUSDT",
              "HMSTRUSDT", "PEOPLEUSDT", "1000SATSUSDT", "BANANAS31USDT", "BROCCOLI714USDT"]


def get(path, **p):
    with urllib.request.urlopen(f"https://fapi.binance.com/fapi/v1/{path}?{urllib.parse.urlencode(p)}", timeout=30) as r:
        return json.loads(r.read())


def main():
    out = BASE / "fut1h"
    listed = {s["symbol"] for s in get("exchangeInfo")["symbols"]}
    for sym in WANT:
        f = out / f"{sym}.json"
        if f.exists() or sym not in listed:
            print(sym, "var" if f.exists() else "Binance'te yok (delist?)"); continue
        rows, cur = [], START
        while cur < END:
            k = get("klines", symbol=sym, interval="1h", startTime=cur, endTime=END - 1, limit=1500)
            if not k: break
            rows += [[int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[7])] for x in k]
            cur = int(k[-1][0]) + H
            time.sleep(0.05)
        if len(rows) > 24 * 30:
            f.write_text(json.dumps(rows, separators=(",", ":"))); print(sym, len(rows), "saat")
    fund_file = BASE / "meme_funding.json"
    funding = json.loads(fund_file.read_text()) if fund_file.exists() else {}
    for sym in sorted(set(MEMES_HELD + WANT)):
        if sym in funding or not (out / f"{sym}.json").exists(): continue
        rates, cur = [], START
        while cur < END:
            k = get("fundingRate", symbol=sym, startTime=cur, endTime=END - 1, limit=1000)
            if not k: break
            rates += [[int(x["fundingTime"]), float(x["fundingRate"])] for x in k]
            cur = int(k[-1]["fundingTime"]) + 1
            if len(k) < 1000: break
        funding[sym] = rates
    fund_file.write_text(json.dumps(funding))
    print("fonlama:", len(funding), "coin")


if __name__ == "__main__":
    main()
