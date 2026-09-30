"""5-minute USD-M futures klines (data.binance.vision monthly archives) for the "frequent dip" test (30 Sep 2026):
every coin that was in the point-in-time daily top-50 between Sep 2024 and Aug 2026, plus BTCUSDT.
Saved as zips under ~/kripto/arastirma-verisi/fut5m/ (resumable: existing files are skipped)."""
import json, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

PIT = Path(r"C:\Users\ASUS-PC\kripto\arastirma-verisi\pit")
OUT = Path(r"C:\Users\ASUS-PC\kripto\arastirma-verisi\fut5m"); OUT.mkdir(parents=True, exist_ok=True)
DL = "https://data.binance.vision/data/futures/um/monthly/klines"
START = int(datetime(2024, 9, 1, tzinfo=timezone.utc).timestamp() * 1000)
daily = {int(k): v for k, v in json.loads((PIT / "daily_top50.json").read_text()).items()}
symbols = sorted({s for d, v in daily.items() if d >= START for s in v} | {"BTCUSDT"})
months = [f"{y}-{m:02d}" for y in (2024, 2025, 2026) for m in range(1, 13) if "2024-09" <= f"{y}-{m:02d}" <= "2026-08"]


def get(sym, month):
    path = OUT / f"{sym}-5m-{month}.zip"
    if path.exists() or path.with_suffix(".404").exists():
        return 0
    url = f"{DL}/{sym}/5m/{sym}-5m-{month}.zip"
    for attempt in range(4):
        try:
            path.write_bytes(urllib.request.urlopen(url, timeout=60).read()); return 1
        except urllib.error.HTTPError as e:
            if e.code == 404:
                path.with_suffix(".404").write_text(""); return 0
            time.sleep(2 + attempt * 3)
        except Exception:
            time.sleep(3 + attempt * 3)
    return 0


t0 = time.time()
jobs = [(s, m) for s in symbols for m in months]
print(f"{len(symbols)} sembol x {len(months)} ay = {len(jobs)} dosya", flush=True)
done = 0
with ThreadPoolExecutor(4) as pool:
    for i, n in enumerate(pool.map(lambda j: get(*j), jobs)):
        done += n
        if i % 500 == 0:
            print(f"  {i}/{len(jobs)} ({round(time.time()-t0)} sn)", flush=True)
print(f"BITTI {done} yeni dosya, {round((time.time()-t0)/60)} dk", flush=True)
