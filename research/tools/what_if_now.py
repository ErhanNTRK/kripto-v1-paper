"""Read-only 'what if the current candle closed right now?' scan with the bot's own code (public market data only,
no keys, no orders). The still-open 2H/4H candle is included as if it closed at the current price."""
import json, os, sys, time
from pathlib import Path
SRC = Path(r"C:\Users\ASUS-PC\kripto\src"); os.chdir(SRC); sys.path.insert(0, str(SRC))
from crypto_v1.github_worker import detect_long_candidates, fetch_all, btc_history_start, load_2h_strategy
from crypto_v1.research_v2 import FOUR_HOUR, TWO_HOUR
from crypto_v1.risk import validate_config
from crypto_v1 import research_v5 as rv5
symbols = json.loads((SRC / "runtime" / "manifest.json").read_text(encoding="utf-8"))["symbols"]
longs = [s for s in symbols if s != "BTCUSDT"]
now = int(time.time() * 1000)
for name, iv, cfg in (("4H", FOUR_HOUR, validate_config(json.loads(Path("config_v5_long.json").read_text(encoding="utf-8")))),
                      ("2H", TWO_HOUR, load_2h_strategy(relax=False))):
    end = now // iv * iv + iv + 1          # include the still-open candle
    start = end - 45 * 86_400_000
    data = fetch_all(symbols + ["BTCUSDT"], start, end, iv, btc_start=btc_history_start(cfg, end, start))
    cands = detect_long_candidates(data, longs, cfg)
    b = rv5.symmetric_features(data["BTCUSDT"], cfg)[-1]
    print(f"\n{name}: BTC {b['c']:.0f} | EMA50 {b.get('ema50') or 0:.0f} | 200g ort {b.get('regime_ma') or 0:.0f} -> "
          f"{len(cands)} aday")
    for c in cands:
        rank = symbols.index(c["symbol"])
        print(f"  {c['symbol']:12} sira {rank:2d} ({'otomatik' if rank <= 30 else 'Telegram onayi'}) | fiyat {c['close']} | "
              f"stop {c['stop']:.6g} (%{100*(1-c['stop']/c['close']):.1f}) | kirilim {c['breaks_up']}")
    # near misses: which gates block the strongest-looking coins
    feats = {s: rv5.symmetric_features(data[s], cfg)[-1] for s in longs if s in data and len(data[s]) > 60}
    ok = [s for s, f in feats.items() if f.get("breaks_up", 0) >= cfg.get("min_breaks", 1)]
    print(f"  kanal kirilimi olan coinler (diger sartlara bakmadan): {', '.join(ok) if ok else 'yok'}")

print("\n=== neden alinmiyor? (kanal kirilimi olanlar) ===")
for name, iv, cfg in (("4H", FOUR_HOUR, validate_config(json.loads(Path("config_v5_long.json").read_text(encoding="utf-8")))),
                      ("2H", TWO_HOUR, load_2h_strategy(relax=False))):
    end = now // iv * iv + iv + 1; start = end - 45 * 86_400_000
    data = fetch_all(["AVAXUSDT", "AAVEUSDT", "ICPUSDT", "BTCUSDT"], start, end, iv, btc_start=btc_history_start(cfg, end, start))
    for s in ("AVAXUSDT", "AAVEUSDT", "ICPUSDT"):
        f = rv5.symmetric_features(data[s], cfg)[-1]
        why = []
        if cfg.get("max_week_gain") is not None and (f.get("week_gain") is None or f["week_gain"] > cfg["max_week_gain"]):
            why.append(f"7 gunde +%{100*(f.get('week_gain') or 0):.0f} (tavan %{100*cfg['max_week_gain']:.0f})")
        if f.get("ichimoku_ok") is not True:
            why.append("Ichimoku tam yukselis degil")
        if f.get("break_level") is not None and f["c"] - f["break_level"] > cfg["max_extension_atr"] * f["atr"]:
            why.append(f"kirilim seviyesinden cok uzak ({(f['c']-f['break_level'])/f['atr']:.1f} ATR > {cfg['max_extension_atr']})")
        if cfg.get("skip_first_time_high") and f.get("range_high") is not None and f["c"] > f["range_high"]:
            why.append("ilk kez gorulen zirve (skip_first_time_high)")
        print(f"{name} {s:10} fiyat {f['c']} | {'; '.join(why) if why else 'engel yok?'}")
