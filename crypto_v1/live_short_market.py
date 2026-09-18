"""Reconstruct short-pilot limits from Binance Futures so Render restarts
fail safely -- mirrors live_market.py's BinanceMarket/summarize_pilot,
adapted for a Futures account (single USDT-margined wallet, not a
per-asset balance list) and short-specific order pairing (open=SELL,
close=BUY reduceOnly)."""
from datetime import datetime, timezone
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor

from .data import INTERVAL, candles, get, universe
from .live_execution import futures_symbol_rules


def _decimal(value):
    return Decimal(str(value or "0"))


def summarize_short_pilot(account, open_orders, orders, config, day_start_ms=0):
    free_usdt = _decimal(account.get("availableBalance"))
    equity = _decimal(account.get("totalMarginBalance") or account.get("totalWalletBalance"))
    live_orders = [o for o in orders if str(o.get("clientOrderId", "")).startswith("kv1f")]
    opens = [o for o in live_orders if o.get("side") == "SELL" and o.get("status") == "FILLED"
            and str(o.get("clientOrderId", "")).startswith("kv1fs")]
    # Any BUY close, regardless of which of the three close reasons produced
    # it (kv1fp protective stop, kv1fe emergency close, or kv1fx the normal
    # automatic trend/emergency-risk exit from live_short_monitor) -- a
    # previous version only recognized kv1fp/kv1fe, silently excluding the
    # most common real exit path from realized_loss_today and weakening
    # the daily-loss circuit breaker in may_open. Mirrors live_market.py's
    # equivalent spot-side filter, which matches generically on "kv1".
    closes = [o for o in live_orders if o.get("side") == "BUY" and o.get("status") == "FILLED"]
    opens_by_suffix = {str(o.get("clientOrderId", ""))[5:]: o for o in opens}
    fee = Decimal(str(config.get("live_fee_buffer_fraction", "0.001")))
    realized_loss = Decimal("0")
    for close in closes:
        if int(close.get("updateTime", close.get("time", 0))) < day_start_ms:
            continue
        suffix = str(close.get("clientOrderId", ""))[5:]
        open_order = opens_by_suffix.get(suffix)
        if open_order:
            received = _decimal(open_order.get("cumQuote"))
            paid = _decimal(close.get("cumQuote"))
            realized_loss += max(Decimal("0"), paid * (Decimal("1") + fee) - received * (Decimal("1") - fee))
    protective = [o for o in open_orders if str(o.get("clientOrderId", "")).startswith("kv1fp")]
    pilot_drawdown = max(Decimal("0"), Decimal(str(config["pilot_capital_usdt"])) - equity)
    return {"open_positions": len({o["symbol"] for o in protective}),
            "opens_today": len({o["clientOrderId"] for o in opens
                                if int(o.get("updateTime", o.get("time", 0))) >= day_start_ms}),
            "realized_loss_today": realized_loss, "pilot_drawdown": pilot_drawdown,
            "free_usdt": free_usdt, "equity": equity}


class BinanceFuturesMarket:
    def __init__(self, config, strategy_config, environment, executor):
        self.config, self.strategy_config = config, strategy_config
        self.environment, self.executor = environment, executor

    def price(self, symbol):
        risk = self.executor.position_risk(symbol)
        for entry in risk:
            if entry.get("symbol") == symbol:
                return Decimal(str(entry["markPrice"]))
        raise RuntimeError("no mark price available for " + symbol)

    def rules(self, symbol):
        info = self.executor.request("GET", {"symbol": symbol}, path="/fapi/v1/exchangeInfo")
        return futures_symbol_rules(info["symbols"][0])

    def pilot_status(self):
        account = self.executor.account()
        open_orders = self.executor.open_orders()
        symbols = set(universe(self.strategy_config))
        symbols.update(o["symbol"] for o in open_orders)
        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0,
                                                   microsecond=0).timestamp() * 1000
        with ThreadPoolExecutor(max_workers=5) as pool:
            batches = list(pool.map(lambda s: self.executor.all_orders(s), symbols))
        orders = [order for batch in batches for order in batch]
        return summarize_short_pilot(account, open_orders, orders, self.config, int(start))

    def live_positions(self):
        positions = []
        for stop in self.executor.open_orders():
            stop_id = str(stop.get("clientOrderId", ""))
            if not stop_id.startswith("kv1fp") or stop.get("side") != "BUY":
                continue
            open_order = self.executor.query(stop["symbol"], "kv1fs" + stop_id.removeprefix("kv1fp"))
            qty = Decimal(str(open_order.get("executedQty", "0")))
            quote = Decimal(str(open_order.get("cumQuote", "0")))
            if qty <= 0 or quote <= 0:
                continue
            positions.append({"symbol": stop["symbol"], "entry": quote / qty,
                              "stop_price": Decimal(str(stop["stopPrice"])),
                              "quantity": stop["origQty"], "stop_client_id": stop_id,
                              "open_time": int(open_order.get("time", open_order.get("updateTime", 0)))})
        return positions

    def analysis(self, position, feature_fn, interval=INTERVAL):
        now = get("time")["serverTime"] // interval * interval
        start = min(position["open_time"], now - 220 * interval)
        coin_rows = candles(position["symbol"], start, now, interval)
        btc_rows = coin_rows if position["symbol"] == "BTCUSDT" else candles("BTCUSDT", start, now, interval)
        coin = feature_fn(coin_rows, self.strategy_config)[-1]
        btc = feature_fn(btc_rows, self.strategy_config)[-1]
        low = min(row["l"] for row in coin_rows if row["t"] >= position["open_time"] // interval * interval)
        return coin, btc, low
