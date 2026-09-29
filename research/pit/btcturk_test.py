"""The user's question (29 Sep): what if the bot's coin list were the coins listed on BtcTurk?
Era 2023-26 only (today's BtcTurk list). Live rules as deployed today (4H: ETH/BTC gate, 2 ATR; 2H: 3 ATR),
two separate pots combined 30/70, realistic costs. Universes per day:
  A  live: the day's point-in-time Binance top-50
  B  A intersected with today's BtcTurk list (only BtcTurk coins that were top-50 that day)
  C  every BtcTurk coin with a Binance perp listed >= 360 days that day, whatever its volume rank
  D  today's fixed Binance top-50 used for the whole period (same survivorship bias as C -> the fair yardstick for C)
Caveat: C and D use TODAY's lists (coins that survived to be listed now) -> both flattered; compare C with D.
23 BtcTurk coins have no price data here (never top-50 in 2023-26, mostly young listings)."""
import os, sys, json, statistics as S
from datetime import datetime, timezone
from pathlib import Path
HERE = Path(__file__).parent
os.environ["PIT_DIR"] = r"C:\Users\ASUS-PC\kripto\arastirma-verisi\pit"
src = (HERE / "package_test.py").read_text(encoding="utf-8").split("PYR = dict(")[0]
ns = {"__file__": str(HERE / "package_test.py")}
exec(compile(src, "package_core", "exec"), ns)
DAY, START, END = ns["DAY"], ns["START"], ns["END"]
ORIG = dict(ns["DAILY"])
bt = {b + "USDT" for b in json.load(open(HERE / "btcturk_bases.json"))}
fm = json.load(open(r"C:\Users\ASUS-PC\kripto\arastirma-verisi\pit\futures_first_month.json"))
listed = {s: int(datetime.strptime(m + "-01", "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
          for s, m in fm.items() if m and m != "HATA"}
today50 = [s for s in json.load(open(r"C:\Users\ASUS-PC\kripto\src\runtime\manifest.json"))["symbols"] if s != "BTCUSDT"]
UNIV = {
    "A canli (gunluk ilk 50)": {d: u for d, u in ORIG.items()},
    "B ilk 50 icindeki BtcTurk coinleri": {d: u & bt for d, u in ORIG.items()},
    "C tum BtcTurk coinleri (360 gun+)": {d: {s for s in bt if s in listed and d - listed[s] >= 360 * DAY} for d in ORIG},
    "D bugunku ilk 50, sabit (C'nin kiyasi)": {d: {s for s in today50 if s in listed and d - listed[s] >= 360 * DAY} for d in ORIG},
}
C4 = ns["validate_config"](json.loads(Path(r"C:\Users\ASUS-PC\Documents\GitHub\kripto-v1-paper\config_v5_long.json").read_text(encoding="utf-8")))
D4 = ns["prepare"]("4h", dict(C4, entry_mode="breakout"))
D2 = ns["prepare"]("2h", dict(ns["load_2h_strategy"](relax=False), entry_mode="breakout"))
ts = lambda y, m: int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)
STARTS = [ts(2023 + (m - 1) // 12, (m - 1) % 12 + 1) for m in range(9, 33)]
YEAR = 365 * DAY
def windows(D, **kw):
    rets, n = [], []
    for s0 in STARTS:
        c, cnt = ns["run"](D, s0=s0, s1=s0 + YEAR, **kw)
        w = [(t, e) for t, e in c if s0 <= t <= s0 + YEAR] or [(s0, 170.0)]
        rets.append(w[-1][1] / 170 - 1); n.append(cnt["giris"])
    return rets, S.mean(n) / 52.14
for name, u in UNIV.items():
    ns["DAILY"].clear(); ns["DAILY"].update(u)
    r4, w4 = windows(D4, trail=6.0, maxpos=6, gate=True)
    r2, w2 = windows(D2, stop_mult=3.0)
    tot = sorted(0.3 * a + 0.7 * b for a, b in zip(r4, r2))
    size = S.mean(len(u[d]) for d in u)
    print(f"{name:40} | gunluk ort {size:5.1f} coin | haftada {w4 + w2:4.1f} islem | 100$ 12 ay sonra: tipik {100*(1+S.median(tot)):5.0f}$ "
          f"en kotu {100*(1+tot[0]):5.0f}$ en iyi {100*(1+tot[-1]):5.0f}$ zararli yil %{100*sum(x<0 for x in tot)/len(tot):3.0f}", flush=True)
print("BITTI")
