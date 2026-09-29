"""Point-in-time universe data from the Binance public archive (data.binance.vision), not the bot's API.

Mirrors the live universe rule (crypto_v1.data.universe + weekly_universe): USDT pairs that trade on Spot
AND have a USDT-M perpetual with the SAME symbol, base not in excluded_bases, futures listed >= 360 days,
ranked by the 7-day average Spot quote volume, top 50 -- recomputed for every day of the test.

Steps (resumable; everything cached under OUT):
  1. list every futures and spot symbol the archive has ever had (delisted ones included)
  2. first futures month per symbol (listing age)
  3. spot 1d klines 2023-07 .. 2026-08 for the intersection -> daily top-50 by 7-day avg quote volume
  4. spot 4h + 2h klines 2023-03 .. 2026-08 for every symbol that was ever in a daily top-50 (+ BTC)
"""
import csv, io, json, re, sys, time, urllib.request, urllib.error, zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path

OUT = Path(r"C:\Users\ASUS-PC\kripto\arastirma-verisi\pit2020"); OUT.mkdir(parents=True, exist_ok=True)
S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
DL = "https://data.binance.vision"
EXCLUDED = set(json.loads(Path(r"C:\Users\ASUS-PC\kripto\src\config_v5_long.json").read_text())["excluded_bases"])
DAY = 86_400_000


def months(a, b):
    y, m = map(int, a.split("-")); out = []
    while f"{y}-{m:02d}" <= b:
        out.append(f"{y}-{m:02d}"); m += 1
        if m == 13: y, m = y + 1, 1
    return out


def s3_list(prefix):
    """All CommonPrefixes / Keys under prefix (paginated)."""
    prefixes, keys, marker = [], [], ""
    while True:
        url = f"{S3}?delimiter=/&prefix={prefix}" + (f"&marker={marker}" if marker else "")
        x = None
        for attempt in range(8):
            try:
                x = urllib.request.urlopen(url, timeout=60).read().decode(); break
            except Exception:
                time.sleep(2 + 3 * attempt)
        if x is None:
            raise RuntimeError("listing failed: " + prefix)
        prefixes += re.findall(r"<Prefix>([^<]+)</Prefix></CommonPrefixes>", x)
        keys += re.findall(r"<Key>([^<]+)</Key>", x)
        if "<IsTruncated>true</IsTruncated>" not in x:
            return prefixes, keys
        nm = re.findall(r"<NextMarker>([^<]+)</NextMarker>", x)
        marker = nm[0] if nm else (keys[-1] if keys else prefixes[-1])


def get(url, path):
    if path.exists() or path.with_suffix(".404").exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(4):
        try:
            path.write_bytes(urllib.request.urlopen(url, timeout=60).read()); return
        except urllib.error.HTTPError as e:
            if e.code == 404:
                path.with_suffix(".404").write_text(""); return
            time.sleep(2)
        except Exception:
            time.sleep(3)


def rows(path):
    if not path.exists():
        return []
    with zipfile.ZipFile(path) as z:
        text = z.read(z.namelist()[0]).decode()
    return [r for r in csv.reader(io.StringIO(text)) if r and r[0].isdigit()]


def step1():
    f = OUT / "symbols.json"
    if f.exists():
        return json.loads(f.read_text())
    fut = [p.rstrip("/").rsplit("/", 1)[1] for p in s3_list("data/futures/um/monthly/klines/")[0]]
    spot = [p.rstrip("/").rsplit("/", 1)[1] for p in s3_list("data/spot/monthly/klines/")[0]]
    fut = {s for s in fut if s.endswith("USDT") and "_" not in s}
    spot = {s for s in spot if s.endswith("USDT")}
    both = sorted(s for s in fut & spot if s[:-4] not in EXCLUDED)
    out = {"futures": len(fut), "spot": len(spot), "both": both}
    f.write_text(json.dumps(out)); print(f"adim 1: futures {len(fut)}, spot {len(spot)}, ikisinde de {len(both)}", flush=True)
    return out


def step2(both):
    f = OUT / "futures_first_month.json"
    have = json.loads(f.read_text()) if f.exists() else {}
    todo = [s for s in both if s not in have]
    def first(s):
        _, keys = s3_list(f"data/futures/um/monthly/klines/{s}/1d/")
        # every monthly file's own month; the FIRST one is the listing month
        ms = sorted(m for k in keys for m in re.findall(r"-(\d{4}-\d{2})\.zip$", k))
        return s, (ms[0] if ms else None)
    def safe(s):
        try:
            return first(s)
        except Exception:
            return s, "HATA"
    for rnd in range(3):
        todo = [s for s in both if have.get(s, "HATA") == "HATA"]
        if not todo:
            break
        with ThreadPoolExecutor(3) as pool:
            for k, (s, m) in enumerate(pool.map(safe, todo)):
                have[s] = m
                if k % 50 == 0:
                    f.write_text(json.dumps(have))
        f.write_text(json.dumps(have))
    bad = [s for s, m in have.items() if m == "HATA"]
    print(f"adim 2: {len(have)} sembolun futures listelenme ayi, basarisiz {len(bad)}", flush=True)
    return have


def step3(both, first_month):
    jobs = [(f"{DL}/data/spot/monthly/klines/{s}/1d/{s}-1d-{m}.zip", OUT / "spot1d" / f"{s}-1d-{m}.zip")
            for s in both for m in months("2020-11", "2023-09")]
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda j: get(*j), jobs))
    vol = {}  # symbol -> {day_ms: quote volume}
    for s in both:
        d = {}
        for m in months("2020-11", "2023-09"):
            for r in rows(OUT / "spot1d" / f"{s}-1d-{m}.zip"):
                t = int(r[0]); t = t // 1000 if t > 10**14 else t   # newer files use microseconds
                d[t] = float(r[7])
        if d:
            vol[s] = d
    start = int(datetime(2021, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    end = int(datetime(2023, 10, 1, tzinfo=timezone.utc).timestamp() * 1000)
    daily = {}
    for day in range(start, end, DAY):
        cands = []
        for s, d in vol.items():
            fm = first_month.get(s)
            if not fm or fm == "HATA":
                continue
            listed = int(datetime.strptime(fm + "-01", "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
            if day - listed < 360 * DAY:
                continue
            last7 = [d.get(day - k * DAY) for k in range(1, 8)]
            if any(v is None for v in last7):   # not trading all of the last 7 days
                continue
            cands.append((-(sum(last7) / 7), s))
        daily[day] = [s for _, s in sorted(cands)[:50]]
    ever = sorted({s for v in daily.values() for s in v})
    (OUT / "daily_top50.json").write_text(json.dumps({str(k): v for k, v in daily.items()}))
    print(f"adim 3: {len(daily)} gun, en az bir gun top-50'de olan {len(ever)} coin", flush=True)
    return daily, ever


def step4(ever):
    syms = sorted(set(ever) | {"BTCUSDT"})
    jobs = [(f"{DL}/data/spot/monthly/klines/{s}/{iv}/{s}-{iv}-{m}.zip", OUT / f"spot{iv}" / f"{s}-{iv}-{m}.zip")
            for s in syms for iv in ("4h", "2h") for m in months("2020-05", "2023-09")]
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda j: get(*j), jobs))
    for iv in ("4h", "2h"):
        data = {}
        for s in syms:
            rs = []
            for m in months("2020-05", "2023-09"):
                for r in rows(OUT / f"spot{iv}" / f"{s}-{iv}-{m}.zip"):
                    t = int(r[0]); t = t // 1000 if t > 10**14 else t
                    rs.append({"t": t, "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4]), "v": float(r[5])})
            if rs:
                rs.sort(key=lambda x: x["t"]); data[s] = rs
        (OUT / f"data_{iv}.json").write_text(json.dumps(data))
        print(f"adim 4: {iv} {len(data)} sembol", flush=True)


if __name__ == "__main__":
    t0 = time.time()
    sy = step1()
    fm = step2(sy["both"])
    daily, ever = step3(sy["both"], fm)
    step4(ever)
    print(f"BITTI {round((time.time() - t0) / 60)} dk", flush=True)
