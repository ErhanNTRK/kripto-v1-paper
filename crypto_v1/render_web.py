"""Render Frankfurt health probe and authenticated Telegram command webhook."""
import hmac, json, os, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from .binance_account import verify_from_environment
from .binance_trade import SpotExecutor
from .live_controller import approve_buy
from .live_execution import execution_enabled
from .live_market import BinanceMarket
from .live_monitor import execute_exit, exit_decision
from .live_signal import fetch_runtime_state
from .research_v2 import DonchianModel, FOUR_HOUR
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
    def __init__(self, config, strategy_config, environment):
        self.config, self.environment = config, environment
        self.executor = SpotExecutor(config, environment)
        self.market = BinanceMarket(config, strategy_config, environment, self.executor)

    def telegram(self, payload):
        parsed = telegram_command(payload, self.environment["TELEGRAM_CHAT_ID"])
        if not parsed: return {"status": "ignored"}
        if parsed["command"].strip().upper() != "AL":
            send_message("Komut reddedildi. Yalniz guncel tek AL sinyali icin AL yazin.")
            return {"status": "rejected", "reason": "invalid_command"}
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

    def scan(self):
        results = []
        for position in self.market.live_positions():
            feature, btc, high = self.market.analysis(position, DonchianModel.features, FOUR_HOUR)
            reason = exit_decision(position, feature, btc, high,
                                   {**self.config, **{"trailing_atr": self.market.strategy_config["trailing_atr"]}},
                                   sell_fn=DonchianModel.sell)
            if not reason: continue
            if not execution_enabled(self.config, self.environment):
                results.append({"status": "preview_exit", "symbol": position["symbol"], "reason": reason})
                continue
            result = execute_exit(position, reason, self.executor)
            send_message(f"OTOMATIK SAT: {position['symbol']} | Neden: {reason}")
            results.append(result)
        return {"status": "scanned", "results": results}

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
    # V2 (hourly Donchian 20/40/80, re-run on 4h bars) drives live candidates and exits;
    # see ARASTIRMA.md for why V1's config.json was dropped from the live path.
    strategy = json.loads(Path("config_v2.json").read_text(encoding="utf-8"))
    global APP
    APP = LiveApp(config, strategy, os.environ)
    telegram_ready = all(os.environ.get(k) for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_WEBHOOK_SECRET"))
    STATUS.update(ready=True, binance_connected=True, telegram_ready=telegram_ready,
                  orders_enabled=execution_enabled(config, os.environ))
    print("Binance connected; orders_enabled=" + str(STATUS["orders_enabled"]), flush=True)
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "10000"))), Handler).serve_forever()

if __name__ == "__main__": main()
