"""One-off cache: aggregate the 5m USD-M futures zips (~/kripto/arastirma-verisi/fut5m, Sep 2024 - Aug 2026)
to 1h bars, one JSON per symbol in ~/kripto/arastirma-verisi/fut1h/ as [t, o, h, l, c, quote_volume] rows.
Existing outputs are skipped, so it can be re-run after new months are downloaded (delete the symbol's file)."""
import io, json, zipfile
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

SRC = Path.home() / "kripto" / "arastirma-verisi" / "fut5m"
DST = Path.home() / "kripto" / "arastirma-verisi" / "fut1h"
H = 3_600_000


def build(symbol):
    out = DST / f"{symbol}.json"
    if out.exists(): return symbol, -1
    bars = {}
    for z in sorted(SRC.glob(f"{symbol}-5m-*.zip")):
        with zipfile.ZipFile(z) as f:
            text = f.read(f.namelist()[0]).decode()
        for line in text.splitlines():
            if not line or not line[0].isdigit(): continue
            p = line.split(",")
            t, o, h, l, c, qv = int(p[0]), float(p[1]), float(p[2]), float(p[3]), float(p[4]), float(p[7])
            k = t // H * H
            b = bars.get(k)
            if b is None: bars[k] = [k, o, h, l, c, qv]
            else:
                b[2] = max(b[2], h); b[3] = min(b[3], l); b[4] = c; b[5] += qv
    rows = [bars[k] for k in sorted(bars)]
    out.write_text(json.dumps(rows, separators=(",", ":")))
    return symbol, len(rows)


if __name__ == "__main__":
    DST.mkdir(parents=True, exist_ok=True)
    symbols = sorted({z.name.split("-5m-")[0] for z in SRC.glob("*-5m-*.zip")})
    with ProcessPoolExecutor(max_workers=10) as pool:
        done = list(pool.map(build, symbols))
    print(len(done), "sembol;", sum(n > 0 for _, n in done), "yeni yazildi")
