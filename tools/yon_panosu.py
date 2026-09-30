"""Yön panosu (direction board), 30 Sep 2026: the user picks trades by hand from a table the bot prepares.

Every 5 minutes: the 50 USDT perpetuals with the most 24h volume on Binance Futures, and for each one the trend on
5m / 1h / 4h / 1d (price vs EMA200, EMA50 vs EMA200, Ichimoku cloud), RSI(14) on 1h, strength against BTC over 24h
and 7 days, the last hour's volume against the day's average, the 24h move, the rise from the 24h low (pump) and
the funding rate. A header shows BTC's own state and ETH/BTC against its 50-day EMA (the bot's bad-period gate).

Public market data only: no API key, no account access, no orders. Writes an HTML page that reloads itself;
run it with "Yon Panosu.bat" (or: python tools/yon_panosu.py [--once])."""
import json, sys, time, urllib.request, urllib.parse, webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from html import escape
from pathlib import Path

BASE = "https://fapi.binance.com/fapi/v1/"
OUT = Path.home() / "kripto" / "pano" / "yon.html"
TOP_N, EVERY_S = 50, 300
TFS = (("5m", "5dk"), ("1h", "1s"), ("4h", "4s"), ("1d", "1g"))
WEIGHT = {"5m": 1, "1h": 1, "4h": 2, "1d": 2}
SKIP = {"USDCUSDT", "FDUSDUSDT", "BTCDOMUSDT", "PAXGUSDT", "XAUTUSDT"}   # stables, index, gold


def get(path, params=None):
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                return json.load(r)
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2)


def ema(xs, n):
    out, e, k = [], None, 2 / (n + 1)
    for x in xs:
        e = x if e is None else e + (x - e) * k
        out.append(e)
    return out


def rsi(closes, n=14):
    gains = losses = 0.0
    for a, b in zip(closes[-n - 1:-1], closes[-n:]):
        d = b - a
        gains += max(d, 0); losses += max(-d, 0)
    return 100.0 if losses == 0 else 100 - 100 / (1 + gains / losses)


def cloud(H, L, C):
    """Ichimoku at the last bar: +1 above the cloud, -1 below, 0 inside; and tenkan vs kijun."""
    def mid(a, b, n, end):
        return (max(a[end - n + 1:end + 1]) + min(b[end - n + 1:end + 1])) / 2
    i, j = len(C) - 1, len(C) - 1 - 26
    if j - 52 < 0:
        return 0, 0
    span_a = (mid(H, L, 9, j) + mid(H, L, 26, j)) / 2
    span_b = mid(H, L, 52, j)
    top, bot = max(span_a, span_b), min(span_a, span_b)
    pos = 1 if C[i] > top else -1 if C[i] < bot else 0
    tk = 1 if mid(H, L, 9, i) > mid(H, L, 26, i) else -1
    return pos, tk


def frame(symbol, interval):
    k = get("klines", {"symbol": symbol, "interval": interval, "limit": 250})
    H = [float(r[2]) for r in k]; L = [float(r[3]) for r in k]; C = [float(r[4]) for r in k]
    V = [float(r[7]) for r in k]
    e50, e200 = ema(C, 50), ema(C, 200)
    pos, tk = cloud(H, L, C)
    votes = (1 if C[-1] > e200[-1] else -1) + (1 if e50[-1] > e200[-1] else -1) + pos
    return {"close": C[-1], "e200": e200[-1], "vote": votes, "cloud": pos, "tk": tk, "closes": C, "vol": V}


def arrow(v):
    return ("&#9650;", "up") if v >= 2 else ("&#9660;", "down") if v <= -2 else ("&#9654;", "flat")


def scan():
    tick = get("ticker/24hr")
    info = {s["symbol"]: s for s in get("exchangeInfo")["symbols"]}
    fund = {p["symbol"]: float(p.get("lastFundingRate") or 0) for p in get("premiumIndex")}
    perp = [t for t in tick if t["symbol"].endswith("USDT") and t["symbol"] not in SKIP
            and info.get(t["symbol"], {}).get("contractType") == "PERPETUAL"
            and info.get(t["symbol"], {}).get("status") == "TRADING"]
    perp.sort(key=lambda t: -float(t["quoteVolume"]))
    symbols = ["BTCUSDT"] + [t["symbol"] for t in perp if t["symbol"] != "BTCUSDT"][:TOP_N]
    tmap = {t["symbol"]: t for t in tick}
    jobs = [(s, tf) for s in symbols for tf, _ in TFS]
    with ThreadPoolExecutor(6) as pool:
        frames = dict(zip(jobs, pool.map(lambda j: _safe(frame, *j), jobs)))
    eth_d = _safe(frame, "ETHUSDT", "1d") if "ETHUSDT" not in symbols else frames[("ETHUSDT", "1d")]
    btc = {tf: frames[("BTCUSDT", tf)] for tf, _ in TFS}
    rows = []
    for s in symbols[1:]:
        f = {tf: frames[(s, tf)] for tf, _ in TFS}
        if any(v is None for v in f.values()) or any(v is None for v in btc.values()):
            continue
        c1h, b1h = f["1h"]["closes"], btc["1h"]["closes"]
        rel24 = (c1h[-1] / c1h[-25] - 1) - (b1h[-1] / b1h[-25] - 1)
        c1d, b1d = f["1d"]["closes"], btc["1d"]["closes"]
        rel7 = (c1d[-1] / c1d[-8] - 1) - (b1d[-1] / b1d[-8] - 1) if len(c1d) >= 8 and len(b1d) >= 8 else 0
        vol_ratio = f["1h"]["vol"][-2] / (sum(f["1h"]["vol"][-26:-2]) / 24 or 1)   # last CLOSED hour vs the 24 before
        t = tmap[s]
        low, last = float(t["lowPrice"]), float(t["lastPrice"])
        score = sum(WEIGHT[tf] * f[tf]["vote"] for tf, _ in TFS) + (2 if rel24 > 0.02 else -2 if rel24 < -0.02 else 0)
        verdict = ("GUCLU LONG" if score >= 14 else "LONG" if score >= 7 else
                   "GUCLU SHORT" if score <= -14 else "SHORT" if score <= -7 else "NOTR")
        rows.append({"s": s[:-4], "price": last, "chg": float(t["priceChangePercent"]),
                     "pump": (last / low - 1) * 100 if low > 0 else 0, "rel24": rel24 * 100, "rel7": rel7 * 100,
                     "vol": vol_ratio, "rsi": rsi(c1h), "fund": fund.get(s, 0) * 100, "score": score,
                     "verdict": verdict, "tf": {tf: f[tf]["vote"] for tf, _ in TFS},
                     "ichi4": f["4h"]["cloud"], "ichi1d": f["1d"]["cloud"]})
    rows.sort(key=lambda r: -r["score"])
    ethbtc = None
    if eth_d and btc["1d"]:
        ratio = [e / b for e, b in zip(eth_d["closes"][-len(btc["1d"]["closes"]):], btc["1d"]["closes"])]
        ethbtc = (ratio[-1], ema(ratio, 50)[-1])
    return btc, ethbtc, rows


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception as exc:
        print(f"  {a}: {exc}", flush=True)
        return None


def fmt(p):
    return f"{p:,.2f}" if p >= 100 else f"{p:.4f}" if p >= 1 else f"{p:.6g}"


def render(btc, ethbtc, rows):
    now = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    b = btc["1h"]
    btc_cells = "".join(
        f'<span class="chip {arrow(btc[tf]["vote"])[1]}">{lbl} {arrow(btc[tf]["vote"])[0]}</span>' for tf, lbl in TFS)
    ichi = {1: "bulutun USTUNDE", 0: "bulutun ICINDE", -1: "bulutun ALTINDA"}
    gate = ""
    if ethbtc:
        ok = ethbtc[0] > ethbtc[1]
        gate = (f'<span class="chip {"up" if ok else "down"}">ETH/BTC {ethbtc[0]:.5f} / EMA50 {ethbtc[1]:.5f} - '
                f'{"altlar guclu (bot long acik)" if ok else "altlar zayif (bot long kapali)"}</span>')
    head = (f'<div class="btc"><b>BTC {fmt(b["close"])}</b> {btc_cells}'
            f'<span class="chip">4s EMA200 {fmt(btc["4h"]["e200"])} &middot; 1g EMA200 {fmt(btc["1d"]["e200"])}</span>'
            f'<span class="chip">Ichimoku 4s: {ichi[btc["4h"]["cloud"]]} &middot; 1g: {ichi[btc["1d"]["cloud"]]}</span>'
            f'{gate}</div>')
    body = []
    for r in rows:
        tf = "".join(f'<td class="{arrow(r["tf"][t])[1]}">{arrow(r["tf"][t])[0]}</td>' for t, _ in TFS)
        vcls = "up" if "LONG" in r["verdict"] else "down" if "SHORT" in r["verdict"] else "flat"
        warn = []
        if r["pump"] > 20: warn.append("pompa")
        if r["rsi"] > 75: warn.append("RSI asiri")
        if r["rsi"] < 25: warn.append("RSI dip")
        if r["fund"] > 0.05: warn.append("fonlama yuksek")
        body.append(
            f'<tr><td class="sym">{escape(r["s"])}</td><td class="{vcls} b">{r["verdict"]}</td><td>{r["score"]:+d}</td>{tf}'
            f'<td>{"&#9650;" if r["ichi4"] > 0 else "&#9660;" if r["ichi4"] < 0 else "&middot;"}'
            f'{"&#9650;" if r["ichi1d"] > 0 else "&#9660;" if r["ichi1d"] < 0 else "&middot;"}</td>'
            f'<td class="{"up" if r["rel24"] > 0 else "down"}">{r["rel24"]:+.1f}</td>'
            f'<td class="{"up" if r["rel7"] > 0 else "down"}">{r["rel7"]:+.1f}</td>'
            f'<td>{r["vol"]:.1f}x</td><td>{r["rsi"]:.0f}</td><td>{fmt(r["price"])}</td>'
            f'<td class="{"up" if r["chg"] > 0 else "down"}">{r["chg"]:+.1f}</td><td>{r["pump"]:.1f}</td>'
            f'<td>{r["fund"]:+.3f}</td><td class="warn">{", ".join(warn)}</td></tr>')
    cols = ["Coin", "Yorum", "Puan", "5dk", "1s", "4s", "1g", "Ichi 4s/1g", "BTC'ye gore 24s %", "BTC'ye gore 7g %",
            "Hacim (son 1s)", "RSI 1s", "Fiyat", "24s %", "24s dipten %", "Fonlama %", "Uyari"]
    ths = "".join(f'<th onclick="sortBy({i})">{c}</th>' for i, c in enumerate(cols))
    return f"""<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta http-equiv="refresh" content="60">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Yon Panosu</title><style>
body{{background:#101418;color:#e6e6e6;font:13px system-ui,Segoe UI,sans-serif;margin:16px}}
h1{{font-size:18px;margin:0 0 6px}} h2{{font-size:15px;margin:8px 0 6px}} table.card td{{text-align:left}} .note{{color:#8a96a3;margin:4px 0 10px}}
.btc{{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-bottom:10px}}
.chip{{background:#1c232b;border-radius:6px;padding:3px 8px}}
table{{border-collapse:collapse;width:100%}} th,td{{padding:4px 6px;border-bottom:1px solid #222a33;text-align:right;white-space:nowrap}}
th{{position:sticky;top:0;background:#161c22;cursor:pointer;color:#aab4be}} td.sym,th:first-child{{text-align:left;font-weight:600}}
.up{{color:#3ecf8e}} .down{{color:#ff6b6b}} .flat{{color:#8a96a3}} .b{{font-weight:700}} .warn{{color:#f5b041;text-align:left}}
.wrap{{overflow-x:auto}}</style></head><body>
<h1>Yon Panosu &middot; {now}</h1>
<div class="note">Sadece bilgi: bot bu sayfadan islem acmaz. Her 5 dakikada yenilenir (sayfa her dakika kendini yeniler).
Oklar: fiyat EMA200 ustu/alti + EMA50/EMA200 + Ichimoku bulutu (3 oyun 2'si ayni yondeyse ok). Puan: 1g ve 4s cift sayilir,
1s ve 5dk tek, BTC'ye gore guc +-2. Basliga tiklayinca siralar.</div>
{head}<!--CARD--><div class="wrap"><table id="t"><thead><tr>{ths}</tr></thead><tbody>{''.join(body)}</tbody></table></div>
<script>function sortBy(i){{const t=document.getElementById('t').tBodies[0];const r=[...t.rows];
const v=x=>{{const s=x.cells[i].innerText.replace(/[x%,]/g,'');const n=parseFloat(s);return isNaN(n)?s:n}};
const d=t.dataset.s==i?-1:1;t.dataset.s=d==1?i:'';r.sort((a,b)=>{{const p=v(a),q=v(b);return (p>q?1:p<q?-1:0)*-d}});
r.forEach(x=>t.appendChild(x))}}</script></body></html>"""


# ---- Prediction report card (user, 30 Sep 2026: "assume unlimited money, take every long and short the board
# gives at once, and see how many are right after 15 / 30 / 60 minutes"). Every scan's verdict and price go to
# tahminler.jsonl; once 15/30/60 minutes have passed, the 1-minute close at that moment decides it.
LOG, DONE = OUT.parent / "tahminler.jsonl", OUT.parent / "sonuclar.jsonl"
HORIZONS = (15, 30, 60)
COST = 0.10          # % per round trip (market orders), taken off every trade's move
CLASSES = ("GUCLU LONG", "LONG", "NOTR", "SHORT", "GUCLU SHORT")


def _read_jsonl(path):
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def record(rows, t_ms):
    with LOG.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({"t": t_ms, "s": r["s"] + "USDT", "v": r["verdict"], "p": r["price"]}) + "\n")


def evaluate(preds, done, now_ms):
    """Settle every prediction whose horizon has passed; returns the new results."""
    keys = {(d["t"], d["s"], d["h"]) for d in done}
    todo = {}
    for p in preds:
        for h in HORIZONS:
            at = p["t"] + h * 60_000
            if at <= now_ms - 60_000 and (p["t"], p["s"], h) not in keys:
                todo.setdefault(p["s"], []).append((p, h, at))
    new = []
    for sym, items in todo.items():
        start = min(at for _, _, at in items) // 60_000 * 60_000
        k = _safe(lambda: get("klines", {"symbol": sym, "interval": "1m", "startTime": start, "limit": 1000}))
        closes = {int(r[0]): float(r[4]) for r in (k or [])}
        for p, h, at in items:
            c = closes.get(at // 60_000 * 60_000)
            if c is None:
                if now_ms - at > 900 * 60_000:          # too old to fetch in one go: give up on it
                    new.append({"t": p["t"], "s": sym, "h": h, "v": p["v"], "move": None})
                continue
            new.append({"t": p["t"], "s": sym, "h": h, "v": p["v"], "move": (c / p["p"] - 1) * 100})
    if new:
        with DONE.open("a", encoding="utf-8") as f:
            for d in new:
                f.write(json.dumps(d) + "\n")
    return new


def report_card(done):
    """{(class, horizon): (n, right %, mean move in the predicted direction %, net after costs %)}"""
    card = {}
    for cls in CLASSES + ("HEPSI",):
        side = 1 if "LONG" in cls else -1 if "SHORT" in cls else 0
        for h in HORIZONS:
            moves = [d["move"] for d in done if d["h"] == h and d["move"] is not None and (cls == "HEPSI" or d["v"] == cls)]
            if not moves:
                continue
            if side:
                directed = [side * m for m in moves]
                card[(cls, h)] = (len(moves), 100 * sum(m > 0 for m in directed) / len(moves),
                                  sum(directed) / len(moves), sum(directed) / len(moves) - COST)
            else:
                card[(cls, h)] = (len(moves), 100 * sum(m > 0 for m in moves) / len(moves), sum(moves) / len(moves), None)
    return card


def render_card(card, since):
    if not card:
        return '<div class="note">Tahmin karnesi: ilk sonuclar 15 dakika sonra gelir.</div>'
    head = "".join(f"<th>{h} dk</th>" for h in HORIZONS)
    body = []
    for cls in CLASSES + ("HEPSI",):
        cells = []
        for h in HORIZONS:
            c = card.get((cls, h))
            if not c:
                cells.append("<td>-</td>"); continue
            n, right, mean, net = c
            if net is None:
                cells.append(f'<td class="flat">{n} olcum &middot; yukselen %{right:.0f} &middot; ort {mean:+.2f}%</td>')
            else:
                cls_ = "up" if net > 0 else "down"
                cells.append(f'<td class="{cls_}">{n} islem &middot; dogru %{right:.0f} &middot; ort {mean:+.2f}% '
                             f'&middot; masraf sonrasi {net:+.2f}%</td>')
        label = {"NOTR": "NOTR (karsilastirma)", "HEPSI": "TUM COINLER (piyasa)"}.get(cls, cls)
        body.append(f'<tr><td class="sym">{label}</td>{"".join(cells)}</tr>')
    return (f'<h2>Tahmin karnesi <span class="note">({since} tarihinden beri; her tahmin aninda fiyat, 15/30/60 dk sonraki '
            f'1 dakikalik kapanisla karsilastirilir; LONG yukselirse, SHORT duserse dogru; masraf %{COST:.2f})</span></h2>'
            f'<div class="wrap"><table class="card"><thead><tr><th>Yorum</th>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div><br>')


def main():
    once = "--once" in sys.argv
    OUT.parent.mkdir(parents=True, exist_ok=True)
    opened = False
    preds, done = _read_jsonl(LOG), _read_jsonl(DONE)
    while True:
        t0 = time.time()
        try:
            btc, ethbtc, rows = scan()
            now_ms = int(time.time() * 1000)
            done += evaluate(preds, done, now_ms)
            settled = {(d["t"], d["s"]) for d in done if d["h"] == HORIZONS[-1]}
            preds = [p for p in preds if (p["t"], p["s"]) not in settled]
            new = [{"t": now_ms, "s": r["s"] + "USDT", "v": r["verdict"], "p": r["price"]} for r in rows]
            record(rows, now_ms); preds += new
            first = min([d["t"] for d in done] + [now_ms])
            card = render_card(report_card(done), datetime.fromtimestamp(first / 1000).strftime("%d.%m %H:%M"))
            OUT.write_text(render(btc, ethbtc, rows).replace("<!--CARD-->", card), encoding="utf-8")
            print(f"{datetime.now():%H:%M:%S} pano yenilendi: {len(rows)} coin ({time.time()-t0:.0f} sn) -> {OUT}", flush=True)
            if not opened and not once:
                webbrowser.open(OUT.as_uri()); opened = True
        except Exception as exc:
            print(f"{datetime.now():%H:%M:%S} tarama basarisiz: {exc}", flush=True)
        if once:
            break
        time.sleep(max(10, EVERY_S - (time.time() - t0)))


if __name__ == "__main__":
    main()
