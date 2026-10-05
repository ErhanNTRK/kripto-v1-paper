"""Market-crash rebound alarm (user's choice, 5 Oct 2026). Telegram messages only -- it never places an order.

A crash: at least 30% of the top 100 USDT-M perpetuals (24h quote volume) close 20% or more under their own
48-hour high. A rebound: within 24 hours of the crash, BTC's hourly close is 3% or more above its lowest point
since the crash began. The rebound sends the alarm, then the alarm stays quiet for 72 hours.

research/pit/crash_alarm_test.py, 1h futures bars Sep 2024 - Aug 2026: 14 such alarms; over the next 72 hours the
30 biggest coins held equally rose 4.4% on average (12 of 14 up) against -0.6% at any hour; after 7 days the
effect was gone (+1.3%). Runs in its own thread so its ~100 public requests never delay the exit checks."""
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://fapi.binance.com/fapi/v1/"
H = 3_600_000
DROP, SHARE, BOUNCE = 0.20, 0.30, 0.03
CRASH_WINDOW, QUIET = 24 * H, 72 * H
EVERY_S = 1800
STABLE = {"USDC", "FDUSD", "TUSD", "USDP", "DAI", "USDE", "USD1", "BTCDOM", "XAUT", "PAXG", "DEFI"}


def public_get(path, **params):
    query = urllib.parse.urlencode(params)
    with urllib.request.urlopen(f"{API}{path}?{query}", timeout=20) as response:
        return json.loads(response.read())


class CrashAlarm:
    def __init__(self, state_path, get=public_get, send=None, clock=time.time):
        self.path, self.get, self.clock = Path(state_path), get, clock
        if send is None:
            from .telegram import send_message as send
        self.send = send

    def _state(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"crash_at": None, "btc_low": None, "last_alarm": 0}

    def _save(self, state):
        self.path.write_text(json.dumps(state), encoding="utf-8")

    def _hourly(self, symbol, now):
        """Closed 1h bars of the last 48 hours as (open time, high, low, close)."""
        rows = self.get("klines", symbol=symbol, interval="1h", limit=49)
        return [(int(r[0]), float(r[2]), float(r[3]), float(r[4])) for r in rows if int(r[6]) < now]

    def breadth(self, now):
        tickers = self.get("ticker/24hr")
        perps = [t for t in tickers if t["symbol"].isascii() and t["symbol"].endswith("USDT")
                 and t["symbol"][:-4] not in STABLE]
        top = [t["symbol"] for t in sorted(perps, key=lambda t: -float(t["quoteVolume"]))[:100]]
        down, worst = 0, []
        for symbol in top:
            bars = self._hourly(symbol, now)
            if len(bars) < 24: continue
            high, close = max(b[1] for b in bars), bars[-1][3]
            fall = 1 - close / high
            if fall >= DROP:
                down += 1; worst.append((fall, symbol))
        return down, len(top), sorted(worst, reverse=True)

    def tick(self):
        now = int(self.clock() * 1000)
        state = self._state()
        down, total, worst = self.breadth(now)
        btc = self._hourly("BTCUSDT", now)
        if not btc or not total: return None
        crashing = down >= SHARE * total
        if crashing and state["crash_at"] is None and now - state["last_alarm"] > QUIET:
            state.update(crash_at=now, btc_low=min(b[2] for b in btc[-3:]))
            names = ", ".join(f"{s[:-4]} -%{100 * f:.0f}" for f, s in worst[:8])
            self.send(f"[COKUS] Ilk 100 coinin {down}'i 48 saatlik zirvesinin %{int(100 * DROP)}+ altinda. "
                      f"En cok dusenler: {names}. BTC dipten %{int(100 * BOUNCE)} donerse DONUS alarmi gelecek.")
        result = None
        if state["crash_at"] is not None:
            state["btc_low"] = min([state["btc_low"]] + [b[2] for b in btc if b[0] >= state["crash_at"] - H])
            close = btc[-1][3]
            if close >= state["btc_low"] * (1 + BOUNCE) and now - state["last_alarm"] > QUIET:
                self.send(f"[DONUS] Cokusun ardindan BTC dipten %{100 * (close / state['btc_low'] - 1):.1f} dondu "
                          f"({state['btc_low']:.0f} -> {close:.0f}). Gecmis 2 yilda bu alarmdan sonra en buyuk 30 coin "
                          f"72 saatte ortalama +%4.4 yukseldi (14 alarmin 12'si); 7 gunde etki kayboldu. "
                          f"Sadece bilgi: bot bununla alim yapmaz.")
                state.update(last_alarm=now, crash_at=None, btc_low=None); result = "rebound"
            elif now - state["crash_at"] > CRASH_WINDOW and not crashing:
                state.update(crash_at=None, btc_low=None)
        self._save(state)
        return result or ("crash" if crashing else None)


def run_forever(state_path, sleep=time.sleep):
    alarm = CrashAlarm(state_path)
    while True:
        try:
            alarm.tick()
        except Exception as exc:  # a network error must never kill the thread
            print(f"Crash alarm failed: {exc}", flush=True)
        sleep(EVERY_S)
