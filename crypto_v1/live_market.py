"""Reconstruct pilot limits from Binance so Render restarts fail safely."""
from datetime import datetime, timezone
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor

from .binance_account import signed_get
from .data import INTERVAL, candles, get, universe
from .indicators import features
from .live_execution import symbol_rules


def _decimal(value):
    return Decimal(str(value or "0"))


def summarize_pilot(account, open_orders, orders, prices, config, day_start_ms=0):
    balances = account.get("balances", [])
    free_usdt = Decimal("0")
    equity = Decimal("0")
    for balance in balances:
        total = _decimal(balance.get("free")) + _decimal(balance.get("locked"))
        if total <= 0:
            continue
        asset = balance["asset"]
        if asset == "USDT":
            free_usdt = _decimal(balance.get("free"))
            equity += total
        else:
            equity += total * _decimal(prices.get(asset + "USDT", 0))
    live_orders = [o for o in orders if str(o.get("clientOrderId", "")).startswith("kv1")]
    buys = [o for o in live_orders if o.get("side") == "BUY" and o.get("status") == "FILLED"]
    sells = [o for o in live_orders if o.get("side") == "SELL" and o.get("status") == "FILLED"]
    buys_by_suffix = {str(o.get("clientOrderId", ""))[4:]: o for o in buys
                      if str(o.get("clientOrderId", "")).startswith("kv1b")}
    fee = Decimal(str(config.get("live_fee_buffer_fraction", "0.001")))
    realized_loss = Decimal("0")
    for sell in sells:
        if int(sell.get("updateTime", sell.get("time", 0))) < day_start_ms: continue
        suffix = str(sell.get("clientOrderId", ""))[4:]
        buy = buys_by_suffix.get(suffix)
        if buy:
            spent = _decimal(buy.get("cummulativeQuoteQty"))
            received = _decimal(sell.get("cummulativeQuoteQty"))
            realized_loss += max(Decimal("0"), spent * (Decimal("1") + fee)
                                 - received * (Decimal("1") - fee))
    protective = [o for o in open_orders
                  if str(o.get("clientOrderId", "")).startswith("kv1s")]
    pilot_drawdown = max(Decimal("0"), Decimal(str(config["pilot_capital_usdt"])) - equity)
    return {"open_positions": len({o["symbol"] for o in protective}),
            "buys_today": len({o["clientOrderId"] for o in buys
                               if int(o.get("updateTime", o.get("time", 0))) >= day_start_ms}),
            "realized_loss_today": realized_loss, "pilot_drawdown": pilot_drawdown,
            "free_usdt": free_usdt, "equity": equity}


class BinanceMarket:
    def __init__(self, config, strategy_config, environment, executor):
        self.config, self.strategy_config = config, strategy_config
        self.environment, self.executor = environment, executor

    def _account(self):
        return signed_get("/api/v3/account", self.environment["BINANCE_API_KEY"],
                          self.environment["BINANCE_ED25519_PRIVATE_KEY"])

    def _tickers(self):
        rows = get("ticker/24hr")
        return {row["symbol"]: Decimal(row["lastPrice"]) for row in rows}

    def price(self, symbol):
        return self._tickers()[symbol]

    def rules(self, symbol):
        info = get("exchangeInfo", {"symbol": symbol})
        return symbol_rules(info["symbols"][0])

    def pilot_status(self):
        tickers = self._tickers()
        account = self._account()
        open_orders = self.executor.open_orders()
        symbols = set(universe(self.strategy_config))
        symbols.update(o["symbol"] for o in open_orders)
        symbols.update(b["asset"] + "USDT" for b in account.get("balances", [])
                       if b.get("asset") != "USDT" and
                       (_decimal(b.get("free")) + _decimal(b.get("locked"))) > 0 and
                       b["asset"] + "USDT" in tickers)
        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0,
                                                   microsecond=0).timestamp() * 1000
        with ThreadPoolExecutor(max_workers=5) as pool:
            batches = list(pool.map(lambda s: self.executor.all_orders(s), symbols))
        orders = [order for batch in batches for order in batch]
        return summarize_pilot(account, open_orders, orders, tickers, self.config, int(start))

    def live_positions(self):
        positions = []
        for stop in self.executor.open_orders():
            stop_id = str(stop.get("clientOrderId", ""))
            if not stop_id.startswith("kv1s") or stop.get("side") != "SELL": continue
            buy = self.executor.query(stop["symbol"], "kv1b" + stop_id.removeprefix("kv1s"))
            qty = Decimal(str(buy.get("executedQty", "0")))
            quote = Decimal(str(buy.get("cummulativeQuoteQty", "0")))
            if qty <= 0 or quote <= 0: continue
            positions.append({"symbol": stop["symbol"], "entry": quote / qty,
                              "stop_price": Decimal(str(stop["stopPrice"])),
                              "quantity": stop["origQty"], "stop_client_id": stop_id,
                              "buy_time": int(buy.get("time", buy.get("updateTime", 0)))})
        return positions

    def analysis(self, position):
        now = get("time")["serverTime"] // INTERVAL * INTERVAL
        start = min(position["buy_time"], now - 220 * INTERVAL)
        coin_rows = candles(position["symbol"], start, now)
        btc_rows = coin_rows if position["symbol"] == "BTCUSDT" else candles("BTCUSDT", start, now)
        coin = features(coin_rows, self.strategy_config)[-1]
        btc = features(btc_rows, self.strategy_config)[-1]
        high = max(row["h"] for row in coin_rows if row["t"] >= position["buy_time"] // INTERVAL * INTERVAL)
        return coin, btc, high
