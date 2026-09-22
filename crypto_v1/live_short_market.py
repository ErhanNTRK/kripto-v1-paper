"""Reconstruct short-pilot limits from Binance Futures so Render restarts
fail safely -- mirrors live_market.py's BinanceMarket/summarize_pilot,
adapted for a Futures account (single USDT-margined wallet, not a
per-asset balance list) and short-specific order pairing (open=SELL,
close=BUY reduceOnly)."""
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor

from .data import INTERVAL, candles, futures_get, get, universe
from .live_execution import futures_symbol_rules


def _decimal(value):
    return Decimal(str(value or "0"))


def _belongs_to_tag(client_id, prefix_len, tag):
    """See live_market._belongs_to_tag -- same scheme, futures prefixes
    (kv1fs/kv1fp/kv1fx/kv1fe) are 5 chars instead of Spot's 4."""
    if not tag:
        return True
    return str(client_id)[prefix_len:].startswith(str(tag))


# Futures now holds two independent position types sharing one wallet (21
# Sep 2026: leveraged long added alongside the original short) -- open and
# close have opposite sides for each, so a single "opens=SELL, closes=any
# BUY" filter (the pre-long-side version of this function) would silently
# misfile a long open (BUY, kv1fl) as an unmatched short close. Every kind
# of order is now identified by its own distinct 5-char prefix instead.
_SHORT_OPEN_PREFIX = "kv1fs"
_SHORT_STOP_PREFIX = "kv1fp"
_SHORT_CLOSE_PREFIXES = ("kv1fp", "kv1fe", "kv1fx")  # protective stop, emergency close, normal trend/emergency exit
_LONG_OPEN_PREFIX = "kv1fl"
_LONG_STOP_PREFIX = "kv1fq"
_LONG_CLOSE_PREFIXES = ("kv1fq", "kv1fk", "kv1fy")  # protective stop, emergency close, normal trend/emergency exit


def _side_pilot_summary(live_orders, open_side, open_prefix, close_side, close_prefixes, fee, day_start_ms):
    opens = [o for o in live_orders if o.get("side") == open_side and o.get("status") == "FILLED"
            and str(o.get("clientOrderId", "")).startswith(open_prefix)]
    closes = [o for o in live_orders if o.get("side") == close_side and o.get("status") == "FILLED"
             and str(o.get("clientOrderId", "")).startswith(close_prefixes)]
    opens_by_suffix = {str(o.get("clientOrderId", ""))[5:]: o for o in opens}
    realized_loss_today = Decimal("0")
    realized_pnl = Decimal("0")
    for close in closes:
        suffix = str(close.get("clientOrderId", ""))[5:]
        open_order = opens_by_suffix.get(suffix)
        if not open_order:
            continue
        # Whichever of the pair is the SELL leg is what was received;
        # the BUY leg is what was paid -- true regardless of which one
        # is the "open" (short: open=SELL; long: open=BUY).
        sell_order, buy_order = (open_order, close) if open_side == "SELL" else (close, open_order)
        received = _decimal(sell_order.get("cumQuote"))
        paid = _decimal(buy_order.get("cumQuote"))
        pnl = received * (Decimal("1") - fee) - paid * (Decimal("1") + fee)
        realized_pnl += pnl
        if int(close.get("updateTime", close.get("time", 0))) >= day_start_ms:
            realized_loss_today += max(Decimal("0"), -pnl)
    return opens, opens_by_suffix, realized_pnl, realized_loss_today


def summarize_short_pilot(account, open_orders, orders, config, day_start_ms=0, tag=""):
    """tag: see live_market.summarize_pilot's docstring -- same reasoning,
    two systems (e.g. 4H and 2H) sharing the SAME real Futures wallet each
    only own a slice of config["pilot_capital_usdt"], tracked via this
    tag's own all-time realized P&L rather than the whole account's real
    margin balance. Long and short positions share this same wallet/slice
    (21 Sep 2026) -- open_positions/held_symbols/committed/free_usdt are
    combined across both sides, so a symbol held either way still blocks a
    duplicate entry and still counts against the shared capital."""
    account_free_usdt = _decimal(account.get("availableBalance"))
    account_equity = _decimal(account.get("totalMarginBalance") or account.get("totalWalletBalance"))
    live_orders = [o for o in orders if str(o.get("clientOrderId", "")).startswith("kv1f")
                   and _belongs_to_tag(o.get("clientOrderId", ""), 5, tag)]
    fee = Decimal(str(config.get("live_fee_buffer_fraction", "0.001")))
    short_opens, short_by_suffix, short_pnl, short_loss_today = _side_pilot_summary(
        live_orders, "SELL", _SHORT_OPEN_PREFIX, "BUY", _SHORT_CLOSE_PREFIXES, fee, day_start_ms)
    long_opens, long_by_suffix, long_pnl, long_loss_today = _side_pilot_summary(
        live_orders, "BUY", _LONG_OPEN_PREFIX, "SELL", _LONG_CLOSE_PREFIXES, fee, day_start_ms)
    realized_pnl_all_time = short_pnl + long_pnl
    realized_loss_today = short_loss_today + long_loss_today
    protective = [o for o in open_orders
                  if str(o.get("clientOrderId", "")).startswith((_SHORT_STOP_PREFIX, _LONG_STOP_PREFIX))
                  and _belongs_to_tag(o.get("clientOrderId", ""), 5, tag)]
    held_symbols = {o["symbol"] for o in protective}
    # Capital a leveraged position actually ties up is its isolated MARGIN
    # (notional / leverage), not the whole notional -- counting notional
    # (as this did until 22 Sep 2026) let one ~70-90 USDT position "use up"
    # a 34 USDT slice, so free_usdt hit 0 after a single open and the
    # configured max_open_positions was unreachable. Per-symbol leverage
    # comes from the account's own positions list; a symbol not found
    # there falls back to the conservative notional count.
    leverage_by_symbol = {p.get("symbol"): _decimal(p.get("leverage"))
                          for p in account.get("positions", []) if _decimal(p.get("leverage")) > 0}
    committed = Decimal("0")
    for o in protective:
        client_id = str(o.get("clientOrderId", ""))
        by_suffix = short_by_suffix if client_id.startswith(_SHORT_STOP_PREFIX) else long_by_suffix
        notional = _decimal(by_suffix.get(client_id[5:], {}).get("cumQuote"))
        committed += notional / leverage_by_symbol.get(o.get("symbol"), Decimal("1"))
    pilot_capital = Decimal(str(config["pilot_capital_usdt"]))
    if tag:
        equity = pilot_capital + realized_pnl_all_time
        free_usdt = max(Decimal("0"), min(account_free_usdt, equity - committed))
    else:
        equity = account_equity
        free_usdt = account_free_usdt
    pilot_drawdown = max(Decimal("0"), pilot_capital - equity)
    opens_today = {o["clientOrderId"] for o in short_opens + long_opens
                   if int(o.get("updateTime", o.get("time", 0))) >= day_start_ms}
    return {"open_positions": len(held_symbols), "held_symbols": held_symbols,
            "opens_today": len(opens_today),
            "realized_loss_today": realized_loss_today, "pilot_drawdown": pilot_drawdown,
            "free_usdt": free_usdt, "equity": equity}


class BinanceFuturesMarket:
    # See live_market.BinanceMarket's matching cache for why: the raw
    # fetch (account, open orders, per-symbol order history) is tag-
    # agnostic, so it is cached at class level and shared by the 4H and 2H
    # apps behind a lock -- one fetch per 90s window instead of one per
    # app per candidate. Live-observed 18 Sep 2026 as a real contributor
    # to repeated -1003 bans alongside the spot side. The universe scan is
    # kept here (unlike Spot): a closed Futures position leaves no dust
    # balance to find its history by, and /fapi/v1/allOrders is only ~5
    # weight per symbol on the separate Futures rate-limit pool.
    _PILOT_STATUS_CACHE_SECONDS = 90
    _raw_pilot_cache = None
    _raw_pilot_lock = threading.Lock()

    def __init__(self, config, strategy_config, environment, executor, tag="", now=time.time):
        self.config, self.strategy_config = config, strategy_config
        self.environment, self.executor = environment, executor
        self.tag = tag
        self._now = now

    def price(self, symbol):
        """Public mark price. positionRisk (used until 22 Sep 2026) reports
        markPrice "0" for a symbol with NO open position -- i.e. for every
        new entry -- so approve_long_leveraged saw price 0 <= stop and
        rejected every candidate as "entry_price_moved" (live: BCH/HBAR at
        19:00, prices actually well inside the allowed band). Never let a
        zero through: a missing price must fail the attempt, not size it."""
        mark = Decimal(str(futures_get("premiumIndex", {"symbol": symbol}).get("markPrice", "0")))
        if mark <= 0:
            raise RuntimeError("no mark price available for " + symbol)
        return mark

    def rules(self, symbol):
        """Futures exchangeInfo ignores a `symbol` param (unlike Spot's) and
        returns every contract, BTCUSDT first. Bug found 22 Sep 2026: taking
        symbols[0] sized every Futures entry with BTCUSDT's 0.10 tick and 50
        USDT min notional, so a sub-dollar coin's stop rounded down toward 0
        ("rounded plan exceeds risk budget") and pricier coins fell under the
        minimum ("order is below Binance minimums") -- every candidate was
        silently rejected at planning time, no order ever sent."""
        info = self.executor.request("GET", {"symbol": symbol}, path="/fapi/v1/exchangeInfo")
        for entry in info["symbols"]:
            if entry.get("symbol") == symbol:
                return futures_symbol_rules(entry)
        raise ValueError("symbol is not listed on Binance Futures: " + symbol)

    def _raw_pilot_data(self):
        with BinanceFuturesMarket._raw_pilot_lock:
            now = self._now()
            cached = BinanceFuturesMarket._raw_pilot_cache
            if cached is not None and now - cached[0] < self._PILOT_STATUS_CACHE_SECONDS:
                return cached[1:]
            account = self.executor.account()
            open_orders = self.executor.open_orders()
            symbols = set(universe(self.strategy_config))
            symbols.update(o["symbol"] for o in open_orders)
            with ThreadPoolExecutor(max_workers=5) as pool:
                batches = list(pool.map(lambda s: self.executor.all_orders(s), symbols))
            orders = [order for batch in batches for order in batch]
            BinanceFuturesMarket._raw_pilot_cache = (now, account, open_orders, orders)
            return account, open_orders, orders

    def pilot_status(self):
        account, open_orders, orders = self._raw_pilot_data()
        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0,
                                                   microsecond=0).timestamp() * 1000
        return summarize_short_pilot(account, open_orders, orders, self.config, int(start), self.tag)

    def live_positions(self):
        # Recognizes both a short's protective stop (kv1fp, BUY -- buys
        # back to close) and a long's (kv1fq, SELL -- sells to close), 21
        # Sep 2026, tagging each returned position with its own side so
        # the caller (render_web.LiveApp.scan) can apply the right exit
        # logic and executor calls to each.
        positions = []
        for stop in self.executor.open_orders():
            stop_id = str(stop.get("clientOrderId", ""))
            if stop_id.startswith(_SHORT_STOP_PREFIX) and stop.get("side") == "BUY":
                side, open_prefix = "short", _SHORT_OPEN_PREFIX
            elif stop_id.startswith(_LONG_STOP_PREFIX) and stop.get("side") == "SELL":
                side, open_prefix = "long", _LONG_OPEN_PREFIX
            else:
                continue
            if not _belongs_to_tag(stop_id, 5, self.tag):
                continue
            open_order = self.executor.query(stop["symbol"], open_prefix + stop_id[5:])
            qty = Decimal(str(open_order.get("executedQty", "0")))
            quote = Decimal(str(open_order.get("cumQuote", "0")))
            if qty <= 0 or quote <= 0:
                continue
            positions.append({"symbol": stop["symbol"], "side": side, "entry": quote / qty,
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
        # Mirrors live_market.BinanceMarket.analysis's fix (21 Sep 2026):
        # see its comment for why filtering by t >= floor(open_time,
        # interval) alone can be empty right after a fresh entry.
        floor_t = position["open_time"] // interval * interval
        if position.get("side") == "long":
            # Leveraged long (21 Sep 2026): trailing needs the running HIGH
            # since entry (matches live_market.BinanceMarket.analysis's
            # spot-long math), not the low a short needs. Positions without
            # a "side" (pre-existing short-only callers/tests) keep the
            # original short/low behavior.
            since_entry = [row["h"] for row in coin_rows if row["t"] >= floor_t]
            extreme = max(since_entry) if since_entry else coin["h"]
        else:
            since_entry = [row["l"] for row in coin_rows if row["t"] >= floor_t]
            extreme = min(since_entry) if since_entry else coin["l"]
        return coin, btc, extreme
