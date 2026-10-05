"""Weekly pump fade, PAPER (5 Oct 2026): every Monday short the 10 top-100 USDT-M coins that rose most over the last
30 days, while ETH/BTC is above its 50-day EMA and the top 100's median 30-day return is under +10% (the mania rule,
research/pit/mania_and_pair.py: it turned 2021 from x0.23 into x0.97 and kept 2024-26); cover the next Monday 00:00 UTC or at +40% over the entry.
Research: research/pit/pump_fade*.py. Public data only, no keys, no orders.

  python tools/pompa_short.py sec    this week's picks (entry = this Monday's 00:00 UTC open)
  python tools/pompa_short.py izle   mark the open week: stops on 1h highs, P&L at 1x per 10 USDT a coin"""
import json, statistics, sys, time, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = "https://fapi.binance.com/fapi/v1/"
OUT = Path.home() / "kripto" / "pompa_short"; OUT.mkdir(parents=True, exist_ok=True)
DAY, H = 86_400_000, 3_600_000
N, STOP, LOOKBACK = 10, 0.40, 30
STABLE = {"USDC", "FDUSD", "TUSD", "USDP", "DAI", "USDE", "USD1", "BTCDOM", "XAUT", "PAXG", "DEFI"}


def get(path, **p):
    with urllib.request.urlopen(f"{API}{path}?{urllib.parse.urlencode(p)}", timeout=20) as r:
        return json.loads(r.read())


def daily(symbol, limit):
    return [(int(k[0]), float(k[1]), float(k[4]), float(k[7])) for k in get("klines", symbol=symbol, interval="1d", limit=limit)]


def monday(now_ms):
    d = now_ms // DAY
    return (d - datetime.fromtimestamp(d * DAY / 1000, timezone.utc).weekday()) * DAY


def gate_open(week):
    e, b = daily("ETHUSDT", 200), daily("BTCUSDT", 200)
    bm = {t: c for t, _, c, _ in b}
    ratio = [(t, c / bm[t]) for t, _, c, _ in e if t in bm and t < week]  # closes before this Monday
    m = None
    for _, r in ratio: m = r if m is None else m + (r - m) * 2 / 51
    return ratio[-1][1] > m, ratio[-1][1], m


def pick():
    now = int(time.time() * 1000); week = monday(now)
    path = OUT / f"hafta_{datetime.fromtimestamp(week / 1000, timezone.utc):%Y-%m-%d}.json"
    if path.exists(): sys.exit(f"Bu haftanin secimi zaten var: {path}")
    is_open, r, m = gate_open(week)
    tick = get("ticker/24hr")
    perps = [t["symbol"] for t in sorted(tick, key=lambda t: -float(t["quoteVolume"]))
             if t["symbol"].isascii() and t["symbol"].endswith("USDT") and t["symbol"][:-4] not in STABLE][:160]
    rows = []
    for s in perps:
        k = daily(s, LOOKBACK + 2)
        k = [x for x in k if x[0] <= week]
        if len(k) < LOOKBACK + 2 or k[-1][0] != week: continue
        vol30 = sum(x[3] for x in k[-31:-1])
        rise = k[-2][2] / k[-2 - LOOKBACK][2] - 1           # Sunday's close vs 30 days earlier
        rows.append(dict(symbol=s, vol30=vol30, rise=rise, entry=k[-1][1]))   # entry: Monday's 00:00 open
        time.sleep(0.03)
    top100 = sorted(rows, key=lambda x: -x["vol30"])[:100]
    picks = sorted(top100, key=lambda x: -x["rise"])[:N]
    med30 = statistics.median(x["rise"] for x in top100)
    state = dict(week=week, gate_open=is_open, med30=med30, ethbtc=r, ema50=m, picks=[dict(p, stop=p["entry"] * (1 + STOP), status="ACIK")
                                                                         for p in picks], checked=week)
    path.write_text(json.dumps(state, indent=1), encoding="utf-8")
    report(state)


def check(state):
    now = int(time.time() * 1000)
    for p in state["picks"]:
        k = get("klines", symbol=p["symbol"], interval="1h", startTime=state["week"], limit=200)
        closed = [x for x in k if int(x[6]) < now]
        p["last"] = float(k[-1][4]) if k else p["entry"]
        if p["status"] == "ACIK" and any(float(x[2]) >= p["stop"] for x in closed):
            p.update(status="STOP", exit=p["stop"])
    state["checked"] = now


def report(state):
    w = datetime.fromtimestamp(state["week"] / 1000, timezone.utc)
    med = state.get("med30")
    live = state["gate_open"] and (med is None or med < 0.10)
    lines = [f"POMPA SHORT (kagit) | hafta {w:%Y-%m-%d} | {'SISTEM CALISIR' if live else 'BU HAFTA ISLEM YOK'} | kapi "
             f"{'acik' if state['gate_open'] else 'kapali'} (ETH/BTC {state['ethbtc']:.5f}, EMA50 {state['ema50']:.5f}) | "
             f"cilginlik olcusu: ilk 100'un 30g ortanca getirisi %{100 * (med or 0):+.1f} (sinir +%10)"]
    tot = 0.0
    for p in state["picks"]:
        price = p.get("exit", p.get("last", p["entry"]))
        r = 1 - price / p["entry"] - 0.002; tot += r
        lines.append(f"  {p['symbol']:14} 30g +%{100 * p['rise']:5.0f} | giris {p['entry']:.6g} stop {p['stop']:.6g} | {p['status']:5} "
                     f"fiyat {price:.6g} | %{100 * r:+6.1f}")
    lines.append(f"  ORTALAMA (her coine esit, 1x): %{100 * tot / len(state['picks']):+.2f}")
    text = "\n".join(lines)
    print(text)
    (OUT / "rapor.txt").write_text(text, encoding="utf-8")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "sec": return pick()
    files = sorted(OUT.glob("hafta_*.json"))
    if not files: sys.exit("Once: python tools/pompa_short.py sec")
    state = json.loads(files[-1].read_text(encoding="utf-8"))
    check(state); files[-1].write_text(json.dumps(state, indent=1), encoding="utf-8"); report(state)


if __name__ == "__main__":
    main()
