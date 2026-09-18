"""Render Frankfurt health probe and authenticated Telegram command webhook."""
import hmac, json, os, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from .binance_account import verify_from_environment
from .binance_futures import FuturesExecutor
from .binance_trade import SpotExecutor
from .live_controller import approve_buy
from .live_execution import execution_enabled
from .live_market import BinanceMarket
from .live_monitor import execute_exit, exit_decision
from .live_short_controller import approve_short
from .live_short_market import BinanceFuturesMarket
from .live_short_monitor import execute_short_exit, short_exit_decision
from .live_signal import (fetch_runtime_state, fetch_runtime_state_short,
                          pending_candidates, pending_short_candidates)
from .research_v2 import FOUR_HOUR
from .research_v5 import ShortWindowLongModel, symmetric_features
from .telegram import send_message

STATUS = {"ready": False, "binance_connected": False, "orders_enabled": False, "telegram_ready": False}
APP = None

def telegram_command(payload, expected_chat):
    message = payload.get("message", {})
    chat = message.get("chat", {})
    if chat.get("type") != "private" or str(chat.get("id", "")) != str(expected_chat): return None
    text = str(message.get("text", "")).strip()
    if not text: return None
    return {"update_id": int(payload["update_id"]), "command": text}

class LiveApp:
    def __init__(self, config, strategy_config, environment, short_config=None, short_strategy_config=None):
        self.config, self.environment = config, environment
        self.executor = SpotExecutor(config, environment)
        self.market = BinanceMarket(config, strategy_config, environment, self.executor)
        self.short_config = short_config
        # `is not None`, not truthiness: an empty-but-present short_config
        # dict must still enable the short subsystem, not silently disable
        # it the way a falsy `if short_config` check would.
        self.futures_executor = FuturesExecutor(short_config, environment) if short_config is not None else None
        self.futures_market = (BinanceFuturesMarket(short_config, short_strategy_config, environment,
                                                     self.futures_executor)
                               if short_config is not None else None)

    def _approve_long(self, parsed):
        result = approve_buy(parsed["update_id"], parsed["command"], int(time.time() * 1000),
                             fetch_runtime_state(), self.config, self.environment,
                             self.market, self.executor)
        if result["status"] == "preview":
            plan = result["plan"]
            send_message(f"AL onayi dogrulandi: {plan['symbol']} | Planlanan risk {plan['planned_loss_usdt']} USDT. Gercek emir kilidi kapali.")
        elif result["status"] == "bought_and_protected":
            send_message(f"ALIM TAMAMLANDI VE KORUYUCU STOP AKTIF: {result['plan']['symbol']}")
        elif result["status"] == "bought_then_emergency_sold":
            send_message(f"ACIL GUVENLIK SATISI: {result['plan']['symbol']} koruyucu stop kurulamadi ve alim geri satildi.")
        else: send_message("AL yapilmadi: " + result.get("reason", "guvenlik kontrolu"))
        return result

    def _approve_short(self, parsed):
        result = approve_short(parsed["update_id"], parsed["command"], int(time.time() * 1000),
                               fetch_runtime_state_short(), self.short_config, self.environment,
                               self.futures_market, self.futures_executor)
        if result["status"] == "preview":
            plan = result["plan"]
            send_message(f"SHORT onayi dogrulandi: {plan['symbol']} | {plan['leverage']}x kaldirac | "
                         f"Planlanan risk {plan['planned_loss_usdt']} USDT. Gercek emir kilidi kapali.")
        elif result["status"] == "opened_and_protected":
            plan = result["plan"]
            send_message(f"SHORT ACILDI VE KORUYUCU STOP AKTIF: {plan['symbol']} | {plan['leverage']}x kaldirac")
        elif result["status"] == "opened_then_emergency_closed":
            send_message(f"ACIL GUVENLIK KAPATMASI: {result['plan']['symbol']} "
                         f"({result.get('reason')}) short geri kapatildi.")
        else: send_message("SHORT yapilmadi: " + result.get("reason", "guvenlik kontrolu"))
        return result

    def telegram(self, payload):
        """A single "AL" confirms whichever one signal is pending -- long or
        short -- per the user's 18 Sep 2026 preference for one consistent
        reply word instead of separate AL/SHORT commands (the notification
        text itself still says AL_ADAYI or SHORT_ADAYI so the direction is
        never ambiguous to read, only the reply is unified). Both sides'
        pending lists are checked together here so "exactly one signal
        pending" is enforced across the combined set, not just within one
        side -- two simultaneously pending candidates (one long, one short)
        must never be silently resolved by guessing; they fail closed as
        ambiguous, same as two pending candidates on one side today."""
        parsed = telegram_command(payload, self.environment["TELEGRAM_CHAT_ID"])
        if not parsed: return {"status": "ignored"}
        command = parsed["command"].strip().upper()
        if command != "AL":
            send_message("Komut reddedildi. Yalniz guncel tek AL sinyali icin AL yazin.")
            return {"status": "rejected", "reason": "invalid_command"}
        now_ms = int(time.time() * 1000)
        long_pending = pending_candidates(fetch_runtime_state(), now_ms, self.config)
        short_pending = (pending_short_candidates(fetch_runtime_state_short(), now_ms, self.short_config)
                        if self.futures_executor else [])
        total = len(long_pending) + len(short_pending)
        if total != 1:
            reason = "no_pending_signal" if total == 0 else "ambiguous_pending_signals"
            send_message("AL yapilmadi: " + reason)
            return {"status": "rejected", "reason": reason}
        if long_pending:
            return self._approve_long(parsed)
        return self._approve_short(parsed)

    def scan(self):
        results = []
        for position in self.market.live_positions():
            feature, btc, high = self.market.analysis(position, ShortWindowLongModel.features, FOUR_HOUR)
            reason = exit_decision(position, feature, btc, high,
                                   {**self.config, **{"trailing_atr": self.market.strategy_config["trailing_atr"]}},
                                   sell_fn=ShortWindowLongModel.sell)
            if not reason: continue
            if not execution_enabled(self.config, self.environment):
                results.append({"status": "preview_exit", "symbol": position["symbol"], "reason": reason})
                continue
            result = execute_exit(position, reason, self.executor)
            send_message(f"OTOMATIK SAT: {position['symbol']} | Neden: {reason}")
            results.append(result)
        if self.futures_market:
            for position in self.futures_market.live_positions():
                feature, btc, low = self.futures_market.analysis(position, symmetric_features, FOUR_HOUR)
                reason = short_exit_decision(position, feature, btc, low, self.short_config)
                if not reason: continue
                if not execution_enabled(self.short_config, self.environment):
                    results.append({"status": "preview_exit", "symbol": position["symbol"], "reason": reason})
                    continue
                result = execute_short_exit(position, reason, self.futures_executor)
                send_message(f"OTOMATIK SHORT KAPAT: {position['symbol']} | Neden: {reason}")
                results.append(result)
        return {"status": "scanned", "results": results}

def run_periodic_scans(app, interval_seconds=300, sleep=time.sleep, max_iterations=None):
    """Exit-monitor loop independent of GitHub Actions' free-tier cron, whose
    scheduled runs have been observed to lag by hours rather than minutes.
    Runs only while this Render process is warm; a cold free-tier instance
    still needs an external request (health check, webhook, cron) to wake it,
    but does not depend on that request landing on any particular schedule
    to keep scanning once awake. The exchange-native protective stop placed
    at entry time (live_controller.approve_buy) does not depend on this loop
    at all -- it is a real resting order on Binance regardless."""
    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        try:
            app.scan()
        except Exception as exc:
            print(f"Periodic scan failed: {exc}", flush=True)
        iterations += 1
        sleep(interval_seconds)


class Handler(BaseHTTPRequestHandler):
    def _json(self, status, value):
        body = json.dumps(value, default=str).encode(); self.send_response(status)
        self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        if self.path == "/health": self._json(200 if STATUS["ready"] else 503, STATUS); return
        if self.path == "/scan": self._json(200, APP.scan()); return
        self.send_error(404)
    def do_POST(self):
        if self.path != "/telegram": self.send_error(404); return
        expected = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
        supplied = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not expected or not hmac.compare_digest(expected, supplied): self.send_error(403); return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 65536: raise ValueError("invalid body size")
            self._json(200, APP.telegram(json.loads(self.rfile.read(length))))
        except Exception: self._json(500, {"status": "failed_closed"})
    def log_message(self, *args): return

def main():
    verify_from_environment()
    config = json.loads(Path("live_config.json").read_text(encoding="utf-8"))
    # v5 long side (shorter 10/20/40-bar Donchian, uncapped winners via
    # cap_at_target=false) drives live candidates and exits; see ARASTIRMA.md.
    strategy = json.loads(Path("config_v5_long.json").read_text(encoding="utf-8"))
    # v5 short side (Part 2, 18 Sep 2026): same signal mirrored, Binance
    # Futures with leverage tiered 3x/5x by breakout strength. Manual
    # "SHORT" Telegram confirmation to open (matching AL for the long
    # side), automatic exits. Deployed directly to live after the signal
    # passed walk-forward + real funding-cost validation -- see
    # ARASTIRMA.md and the user's explicit 18 Sep 2026 instruction.
    short_config = json.loads(Path("short_live_config.json").read_text(encoding="utf-8"))
    global APP
    APP = LiveApp(config, strategy, os.environ, short_config, strategy)
    telegram_ready = all(os.environ.get(k) for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_WEBHOOK_SECRET"))
    STATUS.update(ready=True, binance_connected=True, telegram_ready=telegram_ready,
                  orders_enabled=execution_enabled(config, os.environ))
    print("Binance connected; orders_enabled=" + str(STATUS["orders_enabled"]), flush=True)
    threading.Thread(target=run_periodic_scans, args=(APP,), daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "10000"))), Handler).serve_forever()

if __name__ == "__main__": main()
