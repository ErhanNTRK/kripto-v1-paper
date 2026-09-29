"""Funding rate + open interest + positioning at entry, for every trade of the LIVE rules (111c1de) on the
point-in-time universe (pit_robust.pkl). Binance archive: daily um/metrics (5-min OI, top-trader and taker
long/short ratios) and monthly um/fundingRate. Only values stamped BEFORE the entry bar's open are used.
Discover on Sep 2023-Aug 2025, validate on Sep 2025-Aug 2026: does any feature split winners from losers?"""
import csv, io, json, pickle, statistics as S, time, urllib.request, urllib.error, zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
OUT = Path(r"C:\Users\ASUS-PC\kripto\arastirma-verisi\oi_funding"); OUT.mkdir(parents=True, exist_ok=True)
DL = "https://data.binance.vision/data/futures/um"
DAY = 86_400_000
res = pickle.load(open(HERE / "pit_robust.pkl", "rb"))
key = next(k for k in res if "CANLI" in k and "kayma" not in k and "risk" not in k)
trades = res[key]["trades"]          # (sys, sym, R, t_in, t_out, pnl, kind)
print(key, len(trades), "islem", flush=True)

day = lambda t: datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m-%d")
month = lambda t: day(t)[:7]


def get(url, path):
    if path.exists() or path.with_suffix(".404").exists():
        return
    for a in range(4):
        try:
            path.write_bytes(urllib.request.urlopen(url, timeout=60).read()); return
        except urllib.error.HTTPError as e:
            if e.code == 404:
                path.with_suffix(".404").write_text(""); return
            time.sleep(2)
        except Exception:
            time.sleep(3)


jobs = set()
for sn, s, r, t_in, *_ in trades:
    for d in (day(t_in), day(t_in - DAY), day(t_in - 3 * DAY)):
        jobs.add((f"{DL}/daily/metrics/{s}/{s}-metrics-{d}.zip", OUT / f"{s}-metrics-{d}.zip"))
    for m in {month(t_in), month(t_in - 9 * 3600 * 1000)}:
        jobs.add((f"{DL}/monthly/fundingRate/{s}/{s}-fundingRate-{m}.zip", OUT / f"{s}-fundingRate-{m}.zip"))
print("indirilecek", len(jobs), flush=True)
with ThreadPoolExecutor(6) as pool:
    list(pool.map(lambda j: get(*j), sorted(jobs)))
print("indirme bitti", flush=True)


def rows(path):
    if not path.exists():
        return []
    with zipfile.ZipFile(path) as z:
        return list(csv.DictReader(io.StringIO(z.read(z.namelist()[0]).decode())))


_m = {}
def metrics(s, d):
    k = (s, d)
    if k not in _m:
        out = []
        for r in rows(OUT / f"{s}-metrics-{d}.zip"):
            try:
                t = int(datetime.strptime(r["create_time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp() * 1000)
                out.append((t, float(r["sum_open_interest"] or 0), float(r["sum_toptrader_long_short_ratio"] or 0),
                            float(r["count_long_short_ratio"] or 0), float(r["sum_taker_long_short_vol_ratio"] or 0)))
            except (ValueError, KeyError):
                pass
        _m[k] = out
    return _m[k]


def last_before(s, t):
    for d in (day(t), day(t - DAY)):
        xs = [x for x in metrics(s, d) if x[0] < t]
        if xs:
            return max(xs)
    return None


_f = {}
def funding_before(s, t):
    for m in (month(t), month(t - 9 * 3600 * 1000)):
        if (s, m) not in _f:
            _f[(s, m)] = sorted((int(r["calc_time"]), float(r["last_funding_rate"])) for r in rows(OUT / f"{s}-fundingRate-{m}.zip"))
    xs = [x for m in (month(t - 9 * 3600 * 1000), month(t)) for x in _f[(s, m)] if x[0] < t]
    return max(xs)[1] if xs else None


feat = []
for sn, s, r, t_in, t_out, pnl, kind in trades:
    now, d1, d3 = last_before(s, t_in), last_before(s, t_in - DAY), last_before(s, t_in - 3 * DAY)
    taker = [x[4] for x in metrics(s, day(t_in)) + metrics(s, day(t_in - DAY)) if t_in - 4 * 3600 * 1000 <= x[0] < t_in]
    f = dict(sn=sn, s=s, R=r, t=t_in, pnl=pnl,
             funding=funding_before(s, t_in),
             oi24=(now[1] / d1[1] - 1) if now and d1 and d1[1] else None,
             oi72=(now[1] / d3[1] - 1) if now and d3 and d3[1] else None,
             top_ls=now[2] if now else None, acct_ls=now[3] if now else None,
             taker4h=S.mean(taker) if taker else None)
    feat.append(f)
json.dump(feat, open(HERE / "oi_funding_features.json", "w"))
split = int(datetime(2025, 9, 1, tzinfo=timezone.utc).timestamp() * 1000)
print(f"\nkapsama: " + ", ".join(f"{k} {sum(x[k] is not None for x in feat)}/{len(feat)}"
                               for k in ("funding", "oi24", "oi72", "top_ls", "acct_ls", "taker4h")))


def table(k):
    xs = [x for x in feat if x[k] is not None]
    if len(xs) < 100:
        print(f"{k}: veri az ({len(xs)})"); return
    disc = sorted(x[k] for x in xs if x["t"] < split)
    cuts = [disc[int(len(disc) * q / 5)] for q in (1, 2, 3, 4)]          # quintile cuts from DISCOVERY only
    b = lambda v: sum(v >= c for c in cuts)
    print(f"\n{k}  (dilim sinirlari kesif donemine gore: {', '.join(f'{c:.4g}' for c in cuts)})")
    print(f"{'dilim':>6} | {'kesif n':>7} {'ort R':>6} {'PF':>5} {'5R+':>4} | {'dogrulama n':>11} {'ort R':>6} {'PF':>5} {'5R+':>4}")
    for q in range(5):
        cells = []
        for part in (lambda x: x["t"] < split, lambda x: x["t"] >= split):
            g = [x for x in xs if part(x) and b(x[k]) == q]
            w = sum(x["pnl"] for x in g if x["pnl"] > 0); l = -sum(x["pnl"] for x in g if x["pnl"] <= 0)
            cells.append(f"{len(g):>7} {S.mean([x['R'] for x in g]) if g else 0:+6.2f} {w/l if l else 0:5.2f} {sum(x['R']>=5 for x in g):>4}")
        print(f"{q+1:>6} | {cells[0]} | {cells[1]:>30}")


for k in ("funding", "oi24", "oi72", "top_ls", "acct_ls", "taker4h"):
    table(k)
print("BITTI", flush=True)
