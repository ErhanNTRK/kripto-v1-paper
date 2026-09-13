"""Reconstruct pilot limits from Binance so Render restarts fail safely."""
from datetime import datetime, timezone
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor

from .binance_account import signed_get
from .data import get, universe
from .live_execution import symbol_rules


def _decimal(value):
    return Decimal(str(value or "0"))


def summarize_pilot(account, open_orders, orders, prices, config):
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
    spent = sum((_decimal(o.get("cummulativeQuoteQty")) for o in buys), Decimal("0"))
    received = sum((_decimal(o.get("cummulativeQuoteQty")) for o in sells), Decimal("0"))
    realized_loss = max(Decimal("0"), spent - received)
    protective = [o for o in open_orders
                  if str(o.get("clientOrderId", "")).startswith("kv1s")]
    pilot_drawdown = max(Decimal("0"), Decimal(str(config["pilot_capital_usdt"])) - equity)
    return {"open_positions": len({o["symbol"] for o in protective}),
            "buys_today": len({o["clientOrderId"] for o in buys}),
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
        symbols = universe(self.strategy_config)
        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0,
                                                   microsecond=0).timestamp() * 1000
        with ThreadPoolExecutor(max_workers=5) as pool:
            batches = list(pool.map(lambda s: self.executor.all_orders(s, start), symbols))
        orders = [order for batch in batches for order in batch]
        return summarize_pilot(self._account(), self.executor.open_orders(), orders,
                               tickers, self.config)
