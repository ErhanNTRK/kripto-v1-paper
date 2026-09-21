"""Reconstruct pilot limits from Binance so Render restarts fail safely."""
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor

from .binance_account import signed_get
from .data import INTERVAL, candles, get
from .indicators import features
from .live_execution import symbol_rules


def _decimal(value):
    return Decimal(str(value or "0"))


def _belongs_to_tag(client_id, prefix_len, tag):
    """tag="" (default) matches everything -- full backward compatibility
    for a single, untagged system. A real tag (e.g. "4" for the 4H system,
    "2" for 2H) matches only client ids whose numeric suffix (right after
    the alpha prefix, e.g. "kv1b") STARTS WITH that digit -- see
    render_web.LiveApp.auto_enter, which builds update_id as
    int(f"{tag}{candidate_timestamp}") specifically so this can recover
    which system opened an order without a separate side-channel ledger."""
    if not tag:
        return True
    return str(client_id)[prefix_len:].startswith(str(tag))


def summarize_pilot(account, open_orders, orders, prices, config, day_start_ms=0, tag=""):
    """tag: when two systems (e.g. 4H and 2H) share the SAME real Spot
    wallet, each only owns a SLICE of config["pilot_capital_usdt"] and
    must never see -- let alone spend -- the other's share. With a tag,
    equity/free_usdt/pilot_drawdown are computed from THIS TAG's own
    all-time realized P&L (starting capital + net realized gains/losses)
    rather than the whole account's real balance, and free_usdt is capped
    at min(the account's real free balance, this tag's own remaining
    allocation after its own currently-open positions' cost basis) so
    sizing can never commit more than this tag's fair share even though
    the wallet itself has no such boundary. This intentionally ignores
    unrealized P&L of this tag's own open positions (more conservative,
    not less, than mark-to-market: a currently-losing open position does
    not inflate "free" capital, since exchange-native stops already
    bound its risk on their own)."""
    balances = account.get("balances", [])
    account_free_usdt = Decimal("0")
    account_equity = Decimal("0")
    for balance in balances:
        total = _decimal(balance.get("free")) + _decimal(balance.get("locked"))
        if total <= 0:
            continue
        asset = balance["asset"]
        if asset == "USDT":
            account_free_usdt = _decimal(balance.get("free"))
            account_equity += total
        else:
            account_equity += total * _decimal(prices.get(asset + "USDT", 0))
    live_orders = [o for o in orders if str(o.get("clientOrderId", "")).startswith("kv1")
                   and _belongs_to_tag(o.get("clientOrderId", ""), 4, tag)]
    buys = [o for o in live_orders if o.get("side") == "BUY" and o.get("status") == "FILLED"]
    sells = [o for o in live_orders if o.get("side") == "SELL" and o.get("status") == "FILLED"]
    buys_by_suffix = {str(o.get("clientOrderId", ""))[4:]: o for o in buys
                      if str(o.get("clientOrderId", "")).startswith("kv1b")}
    fee = Decimal(str(config.get("live_fee_buffer_fraction", "0.001")))
    realized_loss_today = Decimal("0")
    realized_pnl_all_time = Decimal("0")
    for sell in sells:
        suffix = str(sell.get("clientOrderId", ""))[4:]
        buy = buys_by_suffix.get(suffix)
        if not buy:
            continue
        spent = _decimal(buy.get("cummulativeQuoteQty"))
        received = _decimal(sell.get("cummulativeQuoteQty"))
        pnl = received * (Decimal("1") - fee) - spent * (Decimal("1") + fee)
        realized_pnl_all_time += pnl
        if int(sell.get("updateTime", sell.get("time", 0))) >= day_start_ms:
            realized_loss_today += max(Decimal("0"), -pnl)
    protective = [o for o in open_orders if str(o.get("clientOrderId", "")).startswith("kv1s")
                  and _belongs_to_tag(o.get("clientOrderId", ""), 4, tag)]
    held_symbols = {o["symbol"] for o in protective}
    committed = sum((_decimal(buys_by_suffix.get(str(o.get("clientOrderId", ""))[4:], {})
                              .get("cummulativeQuoteQty")) for o in protective), Decimal("0"))
    pilot_capital = Decimal(str(config["pilot_capital_usdt"]))
    if tag:
        equity = pilot_capital + realized_pnl_all_time
        free_usdt = max(Decimal("0"), min(account_free_usdt, equity - committed))
    else:
        equity = account_equity
        free_usdt = account_free_usdt
    pilot_drawdown = max(Decimal("0"), pilot_capital - equity)
    return {"open_positions": len(held_symbols), "held_symbols": held_symbols,
            "buys_today": len({o["clientOrderId"] for o in buys
                               if int(o.get("updateTime", o.get("time", 0))) >= day_start_ms}),
            "realized_loss_today": realized_loss_today, "pilot_drawdown": pilot_drawdown,
            "free_usdt": free_usdt, "equity": equity}


class BinanceMarket:
    # pilot_status() reconstructs this tag's limits from the account, its
    # open orders and the order history of every symbol it may have traded
    # (all_orders() is ~20 Binance weight per symbol, and the signed Spot
    # pool is 6000/min per IP). It runs once per pending candidate
    # approve_buy() evaluates, and with two tagged systems (4H/2H) sharing
    # one wallet a single tick fires it from BOTH LiveApps within seconds.
    # Live-observed 18 Sep 2026: that burst (formerly ~50 symbols x 20
    # weight, per app, per tick) was the actual trigger behind repeated
    # -1003 bans, not candidate volume. Two things keep it small now:
    #
    # 1. The raw fetch is tag-agnostic (same account, same orders), so it is
    #    cached at CLASS level and shared by every instance -- the 4H and
    #    2H apps reuse one fetch instead of each doing their own. A lock
    #    serializes concurrent callers (the periodic thread and an external
    #    /scan can land at the same instant) so they never double-fetch.
    #    Kept short (90s) so a stale free_usdt/held_symbols reading is never
    #    trusted for more than one automatic-entry cycle; position safety
    #    comes from the exchange-native protective stop, not this cache.
    # 2. Only symbols with an open order or a non-zero balance are scanned,
    #    not the whole ~50-symbol scanning universe. Every exit path sells
    #    executedQty * 0.998 (live_controller/live_monitor keep 0.2% back
    #    for base-asset commission), so a traded symbol always leaves dust
    #    behind and stays in the balance list -- its history is still found
    #    after a restart without touching the 40+ symbols never traded.
    #    (A symbol the user manually dust-converts drops out of all-time
    #    realized P&L; free_usdt is still capped by the real account balance
    #    either way, so that can never overspend the wallet.)
    _PILOT_STATUS_CACHE_SECONDS = 90
    _raw_pilot_cache = None
    _raw_pilot_lock = threading.Lock()

    def __init__(self, config, strategy_config, environment, executor, tag="", now=time.time):
        self.config, self.strategy_config = config, strategy_config
        self.environment, self.executor = environment, executor
        self.tag = tag
        self._now = now

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

    def _raw_pilot_data(self):
        with BinanceMarket._raw_pilot_lock:
            now = self._now()
            cached = BinanceMarket._raw_pilot_cache
            if cached is not None and now - cached[0] < self._PILOT_STATUS_CACHE_SECONDS:
                return cached[1:]
            tickers = self._tickers()
            account = self._account()
            open_orders = self.executor.open_orders()
            symbols = {o["symbol"] for o in open_orders}
            symbols.update(b["asset"] + "USDT" for b in account.get("balances", [])
                           if b.get("asset") != "USDT" and
                           (_decimal(b.get("free")) + _decimal(b.get("locked"))) > 0 and
                           b["asset"] + "USDT" in tickers)
            with ThreadPoolExecutor(max_workers=5) as pool:
                batches = list(pool.map(lambda s: self.executor.all_orders(s), symbols))
            orders = [order for batch in batches for order in batch]
            BinanceMarket._raw_pilot_cache = (now, tickers, account, open_orders, orders)
            return tickers, account, open_orders, orders

    def pilot_status(self):
        tickers, account, open_orders, orders = self._raw_pilot_data()
        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0,
                                                   microsecond=0).timestamp() * 1000
        return summarize_pilot(account, open_orders, orders, tickers, self.config, int(start), self.tag)

    def live_positions(self):
        positions = []
        for stop in self.executor.open_orders():
            stop_id = str(stop.get("clientOrderId", ""))
            if not stop_id.startswith("kv1s") or stop.get("side") != "SELL": continue
            if not _belongs_to_tag(stop_id, 4, self.tag): continue
            buy = self.executor.query(stop["symbol"], "kv1b" + stop_id.removeprefix("kv1s"))
            qty = Decimal(str(buy.get("executedQty", "0")))
            quote = Decimal(str(buy.get("cummulativeQuoteQty", "0")))
            if qty <= 0 or quote <= 0: continue
            positions.append({"symbol": stop["symbol"], "entry": quote / qty,
                              "stop_price": Decimal(str(stop["stopPrice"])),
                              "quantity": stop["origQty"], "stop_client_id": stop_id,
                              "buy_time": int(buy.get("time", buy.get("updateTime", 0)))})
        return positions

    def analysis(self, position, feature_fn=None, interval=INTERVAL):
        feature_fn = feature_fn or features
        now = get("time")["serverTime"] // interval * interval
        start = min(position["buy_time"], now - 220 * interval)
        coin_rows = candles(position["symbol"], start, now, interval)
        btc_rows = coin_rows if position["symbol"] == "BTCUSDT" else candles("BTCUSDT", start, now, interval)
        coin = feature_fn(coin_rows, self.strategy_config)[-1]
        btc = feature_fn(btc_rows, self.strategy_config)[-1]
        # Bug found live 21 Sep 2026, first day scan() ran minutes (not
        # hours) after a fresh entry: a bar's "t" is its OPEN time, but a
        # candle used for entry CLOSES at floor(buy_time, interval) -- its
        # own open time is one interval EARLIER. Filtering coin_rows for
        # t >= floor(buy_time, interval) therefore excludes the entry
        # candle itself AND the still-forming next candle (not closed yet,
        # so not even in coin_rows), leaving nothing until a further full
        # candle closes -- max() on that empty result crashed every tick
        # for a real just-opened position ("max() iterable argument is
        # empty"). Falls back to the entry candle's own high (coin["h"],
        # the last available closed bar) when nothing has closed since
        # entry yet: a safe, momentarily-conservative reference that
        # self-corrects the moment real post-entry data exists.
        since_entry = [row["h"] for row in coin_rows
                       if row["t"] >= position["buy_time"] // interval * interval]
        high = max(since_entry) if since_entry else coin["h"]
        return coin, btc, high
