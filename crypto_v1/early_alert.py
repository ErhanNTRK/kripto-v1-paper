"""Early-entry alert (user's request, 6 Oct 2026): the bot only looked at 2H/4H candles once they closed, so a breakout
that happened mid-candle waited up to two hours. This module re-runs the bot's own long detector every minute on the
still-forming candle and sends a Telegram message the first time a coin qualifies. Telegram messages only -- it never
places an order, and the existing AL approval flow is untouched.

Caveat shown in every message: a forming candle can still fall back under the breakout level before it closes, so
this is a heads-up, not a confirmed signal. History is fetched once, then only the last few bars per symbol are
refreshed each minute (about 100 small public requests a minute, far under Binance's IP budget)."""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .data import candles
from .github_worker import FETCH_WORKERS, btc_history_start, fetch_all, load_2h_strategy
from .research_v2 import FOUR_HOUR, TWO_HOUR
from .risk import validate_config
from .short_signal import detect_long_candidates

EVERY_S = 60
DAY = 86_400_000


def _systems():
    four = validate_config(json.loads(Path("config_v5_long.json").read_text(encoding="utf-8")))
    return (("4H", FOUR_HOUR, four), ("2H", TWO_HOUR, load_2h_strategy(relax=False)))


class EarlyAlert:
    def __init__(self, runtime_dir, send=None, clock=time.time):
        self.runtime, self.clock = Path(runtime_dir), clock
        self.state_path = self.runtime / "early_alert.json"
        if send is None:
            from .telegram import send_message as send
        self.send = send
        self.data = {}
        try:
            self.sent = set(json.loads(self.state_path.read_text(encoding="utf-8")).get("sent", []))
        except (OSError, ValueError):
            self.sent = set()

    def _symbols(self):
        manifest = json.loads((self.runtime / "manifest.json").read_text(encoding="utf-8"))
        return manifest["symbols"]

    def _refresh(self, name, interval, config, symbols, now):
        end = now // interval * interval + interval + 1       # include the still-forming candle
        if name not in self.data:
            start = end - 45 * DAY
            self.data[name] = fetch_all(symbols + ["BTCUSDT"], start, end, interval,
                                        btc_start=btc_history_start(config, end, start))
            return
        book = self.data[name]

        def update(symbol):
            rows = book.get(symbol, [])
            since = rows[-1]["t"] if rows else end - 45 * DAY
            fresh = candles(symbol, since, end, interval)
            if fresh:
                rows = [r for r in rows if r["t"] < fresh[0]["t"]] + fresh
            return symbol, rows

        with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
            for symbol, rows in pool.map(update, sorted(set(symbols + ["BTCUSDT"]))):
                book[symbol] = rows

    def tick(self):
        now = int(self.clock() * 1000)
        symbols = self._symbols()
        longs = [s for s in symbols if s != "BTCUSDT"]
        alerts = []
        for name, interval, config in _systems():
            self._refresh(name, interval, config, symbols, now)
            bar_open = now // interval * interval
            for cand in detect_long_candidates(self.data[name], longs, config):
                key = f"{name}:{cand['symbol']}:{bar_open}"
                if key in self.sent:
                    continue
                self.sent.add(key)
                close_in = (bar_open + interval - now) // 60_000
                alerts.append(f"[ERKEN {name}] {cand['symbol']} su an {cand['close']:.6g} -- kirilim var "
                              f"({cand.get('breaks_up', '?')} kanal), stop {cand['stop']:.6g} "
                              f"(%{100 * (1 - cand['stop'] / cand['close']):.1f}). Mum {close_in} dk sonra kapaniyor; "
                              f"kapanisa kadar geri donebilir. Botun AL sorusu kapanista gelir; "
                              f"beklemek istemezsen elle gir. Sadece bilgi.")
        keep = {k for k in self.sent if int(k.rsplit(":", 1)[1]) > now - 2 * DAY}
        self.sent = keep
        self.state_path.write_text(json.dumps({"sent": sorted(keep)}), encoding="utf-8")
        for message in alerts:
            self.send(message)
        return len(alerts)


def run_forever(runtime_dir, sleep=time.sleep):
    alert = EarlyAlert(runtime_dir)
    while True:
        try:
            alert.tick()
        except Exception as exc:  # a network error must never kill the thread
            print(f"Early alert failed: {exc}", flush=True)
        sleep(EVERY_S)
