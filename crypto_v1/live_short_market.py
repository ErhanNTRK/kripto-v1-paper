"""Reconstruct short-pilot limits from Binance Futures so Render restarts
fail safely -- mirrors live_market.py's BinanceMarket/summarize_pilot,
adapted for a Futures account (single USDT-margined wallet, not a
per-asset balance list) and short-specific order pairing (open=SELL,
close=BUY reduceOnly)."""
import json
import threading
import time
import zlib
from datetime import datetime, timezone
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .binance_trade import OrderRejected
from .data import INTERVAL, candles, futures_get, get, universe
from .live_execution import futures_symbol_rules
from .telegram import send_message

DAY_MS = 86_400_000
WEEK_MS = 7 * DAY_MS
# Binance serves income history about 3 months back; asking earlier fails.
INCOME_HISTORY_MS = 89 * DAY_MS
# How long our own filled orders are kept on disk (A7): far past any hold.
ORDER_STORE_MS = 120 * DAY_MS
_ORDER_FIELDS = ("symbol", "orderId", "clientOrderId", "side", "status", "type", "executedQty",
                 "cumQuote", "avgPrice", "time", "updateTime", "reduceOnly")


def _decimal(value):
    return Decimal(str(value or "0"))


def _load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _save(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value), encoding="utf-8")
    tmp.replace(path)


def _order_time(order):
    return int(order.get("updateTime") or order.get("time") or 0)


def _left_open(open_order, fills):
    """What the bot's opening order should still have open now: its filled
    quantity (signed), less every later fill in the symbol that reduced it
    -- ours, a stop's, or a partial close by hand. None when the bot's
    position ended (reached zero or flipped: anything open now came later)
    or was added to (the bot never adds, so that was done by hand)."""
    sign = Decimal("1") if open_order.get("side") == "BUY" else Decimal("-1")
    running = sign * _decimal(open_order.get("executedQty"))
    later = sorted((o for o in fills if o.get("orderId") != open_order.get("orderId")
                    and _order_time(o) > _order_time(open_order)), key=_order_time)
    for order in later:
        move = (Decimal("1") if order.get("side") == "BUY" else Decimal("-1")) * _decimal(order.get("executedQty"))
        if (move > 0) == (sign > 0):
            return None
        running += move
        if running == 0 or (running > 0) != (sign > 0):
            return None
    return running


def symbol_code(symbol):
    """Four digits that make a client order id unique per SYMBOL. Until 23
    Sep 2026 an id was tag + signal time only, and every candidate of the
    same candle shares that time: BCH, LTC and MARSCOIN opened at 23:00 all
    carried kv1fl21790107200000, so the protective stop of the first one
    answered for all three (only BCH ever got a stop) and cancelling one
    cancelled another's."""
    return f"{zlib.crc32(str(symbol).encode()) % 10000:04d}"


def base_suffix(client_id):
    """The id after its 5-char prefix, without a replacement stop's "t<time>"
    ending (render_web._fresh_stop_id). Every lookup that pairs a stop with
    its opening order must use this: with the ending left on, a position
    whose stop had been moved matched no opening order, so its margin
    counted as 0 against the system's capital and its entry time fell back
    to Binance's last position update."""
    return str(client_id)[5:].split("t", 1)[0]


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
    # Keyed by SYMBOL and suffix: ids carried no symbol before 23 Sep 2026,
    # so one suffix can belong to several symbols' orders and pairing by
    # suffix alone mixed up whole positions' P&L.
    opens_by_suffix = {(o.get("symbol"), base_suffix(o.get("clientOrderId", ""))): o for o in opens}
    realized_loss_today = Decimal("0")
    realized_pnl_today = Decimal("0")
    realized_pnl = Decimal("0")
    for close in closes:
        suffix = base_suffix(close.get("clientOrderId", ""))
        symbol = close.get("symbol")
        open_order = opens_by_suffix.get((symbol, suffix))
        if not open_order:
            # An adopted position's stop carries a symbol code its (older)
            # opening order does not, so fall back to this symbol's most
            # recent open before the close.
            candidates = [o for key, o in opens_by_suffix.items() if key[0] == symbol
                          and int(o.get("time", o.get("updateTime", 0))) <= int(close.get("time", close.get("updateTime", 0)))]
            if not candidates:
                continue
            open_order = max(candidates, key=lambda o: int(o.get("time", o.get("updateTime", 0))))
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
            realized_pnl_today += pnl
    return opens, opens_by_suffix, realized_pnl, realized_loss_today, realized_pnl_today


def _closed_symbols(live_orders, day_start_ms):
    """Symbols one of our own close orders (stop, exit, emergency) filled
    today."""
    return {o.get("symbol") for o in live_orders
            if o.get("status") == "FILLED"
            and str(o.get("clientOrderId", "")).startswith(_SHORT_CLOSE_PREFIXES + _LONG_CLOSE_PREFIXES)
            and int(o.get("updateTime", o.get("time", 0))) >= day_start_ms}


def summarize_short_pilot(account, open_orders, orders, config, day_start_ms=0, tag="", net_deposits=None):
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
    short_opens, short_by_suffix, short_pnl, short_loss_today, short_pnl_today = _side_pilot_summary(
        live_orders, "SELL", _SHORT_OPEN_PREFIX, "BUY", _SHORT_CLOSE_PREFIXES, fee, day_start_ms)
    long_opens, long_by_suffix, long_pnl, long_loss_today, long_pnl_today = _side_pilot_summary(
        live_orders, "BUY", _LONG_OPEN_PREFIX, "SELL", _LONG_CLOSE_PREFIXES, fee, day_start_ms)
    realized_pnl_all_time = short_pnl + long_pnl
    realized_loss_today = short_loss_today + long_loss_today
    protective = [o for o in open_orders
                  if str(o.get("clientOrderId", "")).startswith((_SHORT_STOP_PREFIX, _LONG_STOP_PREFIX))
                  and _belongs_to_tag(o.get("clientOrderId", ""), 5, tag)]
    # Real positions on the exchange count too, not just the ones we can
    # see by their protective stop. On 22 Sep 2026 every stop failed to
    # place (Binance -4120), so held_symbols was empty while six real
    # positions were open -- and the same symbol got bought a second time
    # because may_open saw nothing held. Positions are not tag-attributable,
    # so BOTH systems treat them as held and as committed capital: the
    # conservative direction (never double up, never over-commit the shared
    # wallet), even though it can block a symbol the other system opened.
    # ...but a position whose protective stop carries the OTHER system's
    # tag is that system's, not this one's: counting it here too left the
    # 4H side (holding 1) seeing 5 of its 6 slots used by the 2H side's
    # positions on 23 Sep 2026 evening. Only positions no system's stop
    # claims still count for both.
    other_systems = ({o["symbol"] for o in open_orders
                      if str(o.get("clientOrderId", "")).startswith((_SHORT_STOP_PREFIX, _LONG_STOP_PREFIX))}
                     - {o["symbol"] for o in protective})
    account_positions = {p["symbol"]: p for p in account.get("positions", [])
                         if abs(_decimal(p.get("positionAmt"))) > 0 and p["symbol"] not in other_systems}
    own_symbols = {o["symbol"] for o in protective} | set(account_positions)
    # The duplicate guard still looks at EVERY open position, whoever owns
    # it: in one-way mode a second buy of a symbol the other system holds
    # would merge into its position and tangle both systems' stops.
    held_symbols = own_symbols | {p["symbol"] for p in account.get("positions", [])
                                  if abs(_decimal(p.get("positionAmt"))) > 0}
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
    counted = set()
    for o in protective:
        # A stop being replaced can briefly rest twice (the new one is placed
        # before the old one is cancelled); the position counts once.
        if o.get("symbol") in counted:
            continue
        counted.add(o.get("symbol"))
        client_id = str(o.get("clientOrderId", ""))
        by_suffix = short_by_suffix if client_id.startswith(_SHORT_STOP_PREFIX) else long_by_suffix
        notional = _decimal(by_suffix.get((o.get("symbol"), base_suffix(client_id)), {}).get("cumQuote"))
        committed += notional / leverage_by_symbol.get(o.get("symbol"), Decimal("1"))
    for symbol, position in account_positions.items():
        if symbol in {o.get("symbol") for o in protective}:
            continue  # already counted above, via its protective stop
        margin = _decimal(position.get("positionInitialMargin")) or _decimal(position.get("initialMargin"))
        if margin <= 0:
            margin = abs(_decimal(position.get("notional"))) / leverage_by_symbol.get(symbol, Decimal("1"))
        committed += margin
    pilot_capital = Decimal(str(config["pilot_capital_usdt"]))
    # pilot_capital_fraction (23 Sep 2026, user's request): this system's
    # slice of the REAL wallet rather than a number frozen in the config, so
    # profits raise the allocation by themselves and losses lower it. It
    # also counts profit this system did not book itself -- a position
    # closed by hand has no kv1f close order, so the old
    # capital + own realized P&L never saw that money at all.
    # pilot_capital_usdt stays the baseline the loss limit is measured from.
    fraction = Decimal(str(config.get("pilot_capital_fraction") or "0"))
    if tag and fraction > 0:
        equity = account_equity * fraction
        free_usdt = max(Decimal("0"), min(account_free_usdt, equity - committed))
    elif tag:
        equity = pilot_capital + realized_pnl_all_time
        free_usdt = max(Decimal("0"), min(account_free_usdt, equity - committed))
    else:
        equity = account_equity
        free_usdt = account_free_usdt
    # The 40% loss limit's baseline (25 Sep 2026 audit, A1). A frozen
    # pilot_capital_usdt stopped meaning anything once equity became a share
    # of the whole wallet: a deposit pushed the limit out of reach, a
    # withdrawal tripped it with no loss at all. With the account's net
    # deposits known, the baseline is this system's share of the money
    # actually put in, so only trading results move it closer to the limit.
    baseline = pilot_capital
    if tag and fraction > 0 and net_deposits is not None and Decimal(str(net_deposits)) > 0:
        baseline = Decimal(str(net_deposits)) * fraction
    pilot_drawdown = max(Decimal("0"), baseline - equity)
    opens_today = {o["clientOrderId"] for o in short_opens + long_opens
                   if int(o.get("updateTime", o.get("time", 0))) >= day_start_ms}
    # This system's own open result, for the daily equity stop: only the
    # positions its own stops protect -- never a hand-opened one, never the
    # other system's.
    protected_symbols = {o.get("symbol") for o in protective}
    own_unrealized_by_symbol = {p.get("symbol"): _decimal(p.get("unrealizedProfit"))
                                for p in account.get("positions", [])
                                if p.get("symbol") in protected_symbols and abs(_decimal(p.get("positionAmt"))) > 0}
    own_unrealized = sum(own_unrealized_by_symbol.values(), Decimal("0"))
    return {"open_positions": len(own_symbols), "held_symbols": held_symbols,
            "opens_today": len(opens_today),
            "realized_loss_today": realized_loss_today, "pilot_drawdown": pilot_drawdown,
            "pilot_baseline": baseline,
            "free_usdt": free_usdt, "equity": equity,
            "realized_pnl_today": short_pnl_today + long_pnl_today, "own_unrealized": own_unrealized,
            "own_unrealized_by_symbol": own_unrealized_by_symbol,
            "opened_symbols_today": {o.get("symbol") for o in short_opens + long_opens
                                     if int(o.get("updateTime", o.get("time", 0))) >= day_start_ms},
            "closed_symbols_today": _closed_symbols(live_orders, day_start_ms)}


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

    # Contract rules change rarely; the full futures exchangeInfo was being
    # downloaded for every entry attempt, stop move and re-protection.
    _RULES_CACHE_SECONDS = 3600

    # Where the bot keeps what Binance forgets: our own filled orders past
    # allOrders' 7-day window (bot_orders.json) and the running transfer
    # total (transfers.json). main() points it at runtime/; None (tests,
    # Render) keeps nothing on disk.
    store_dir = None
    _DEPOSITS_REFRESH_SECONDS = 600
    _deposits_cache = None
    _deposits_lock = threading.Lock()
    # Positions already reported as not the bot's, so one is reported once.
    _foreign_noted = set()

    def __init__(self, config, strategy_config, environment, executor, tag="", now=time.time,
                 universe_fn=None):
        self.config, self.strategy_config = config, strategy_config
        self.environment, self.executor = environment, executor
        self.tag = tag
        self._now = now
        # Which symbols' order history to read. The live process passes the
        # daily-ranked list both systems trade (runtime/manifest.json), so
        # this no longer re-ranks the whole market every 90 seconds.
        self._universe_fn = universe_fn or (lambda: universe(self.strategy_config))
        self._rules_cache = {}

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
        cached = self._rules_cache.get(symbol)
        if cached is not None and self._now() - cached[0] < self._RULES_CACHE_SECONDS:
            return cached[1]
        info = self.executor.request("GET", {"symbol": symbol}, path="/fapi/v1/exchangeInfo")
        for entry in info["symbols"]:
            if entry.get("symbol") == symbol:
                rules = futures_symbol_rules(entry)
                self._rules_cache[symbol] = (self._now(), rules)
                return rules
        raise ValueError("symbol is not listed on Binance Futures: " + symbol)

    def _raw_pilot_data(self):
        with BinanceFuturesMarket._raw_pilot_lock:
            now = self._now()
            cached = BinanceFuturesMarket._raw_pilot_cache
            if cached is not None and now - cached[0] < self._PILOT_STATUS_CACHE_SECONDS:
                return cached[1:]
            account = self.executor.account()
            open_orders = self.executor.open_orders()
            symbols = set(self._universe_fn())
            symbols.update(o["symbol"] for o in open_orders)
            # A position whose symbol has dropped out of the re-ranked
            # universe still needs its own order history -- that is how an
            # unprotected position is attributed to a system (see
            # unprotected_positions) and how its close is paired for P&L.
            symbols.update(p["symbol"] for p in account.get("positions", [])
                           if abs(_decimal(p.get("positionAmt"))) > 0)
            with ThreadPoolExecutor(max_workers=5) as pool:
                batches = list(pool.map(lambda s: self.executor.all_orders(s), symbols))
            orders = self._with_stored_orders([order for batch in batches for order in batch])
            BinanceFuturesMarket._raw_pilot_cache = (now, account, open_orders, orders)
            return account, open_orders, orders

    def _store(self, name):
        return Path(self.store_dir) / name if self.store_dir else None

    def _with_stored_orders(self, orders):
        """allOrders without a start time returns only the last 7 days, so a
        position held longer lost its opening order: it could no longer be
        told apart from a hand-opened one, nor re-protected, nor its close
        paired for P&L (audit A7). Our own filled orders are kept on disk and
        merged back in."""
        path = self._store("bot_orders.json")
        if path is None:
            return orders
        stored = _load(path, [])
        kept = {(o.get("symbol"), o.get("orderId")): o for o in stored}
        changed = False
        for order in orders:
            if order.get("status") == "FILLED" and str(order.get("clientOrderId", "")).startswith("kv1f"):
                key = (order.get("symbol"), order.get("orderId"))
                if key not in kept:
                    kept[key] = {k: order[k] for k in _ORDER_FIELDS if k in order}
                    changed = True
        cutoff = int(self._now() * 1000) - ORDER_STORE_MS
        fresh = {k: o for k, o in kept.items() if _order_time(o) >= cutoff}
        changed = changed or len(fresh) != len(kept) or len(kept) != len(stored)
        kept = fresh
        if changed:
            try:
                _save(path, list(kept.values()))
            except OSError as exc:
                print(f"Order store not saved: {exc}", flush=True)
        seen = {(o.get("symbol"), o.get("orderId")) for o in orders}
        return orders + [o for k, o in kept.items() if k not in seen]

    def net_deposits(self):
        """USDT moved into the Futures wallet minus USDT moved out, for the
        loss limit's baseline (audit A1). Binance serves transfer history 7
        days per request and ~3 months back, so the running total and how far
        it has been read are kept in transfers.json. None when unknown -- the
        limit then falls back to pilot_capital_usdt."""
        path = self._store("transfers.json")
        if path is None:
            return None
        with BinanceFuturesMarket._deposits_lock:
            now = self._now()
            cached = BinanceFuturesMarket._deposits_cache
            if cached is not None and now - cached[0] < self._DEPOSITS_REFRESH_SECONDS:
                return cached[1]
            state = _load(path, None)
            now_ms = int(now * 1000)
            if state:
                total, cursor = Decimal(str(state["total"])), int(state["cursor"])
            else:
                total, cursor = Decimal("0"), now_ms - INCOME_HISTORY_MS
            try:
                start = cursor + 1
                while start <= now_ms:
                    end = min(now_ms, start + WEEK_MS - 1)
                    rows = self.executor.income(start, end, income_type="TRANSFER")
                    if len(rows) >= 1000:
                        raise RuntimeError("too many transfers in one week to total safely")
                    total += sum((_decimal(r.get("income")) for r in rows
                                  if r.get("asset", "USDT") == "USDT"), Decimal("0"))
                    start = end + 1
            except Exception as exc:
                print(f"Transfer history not read: {exc}", flush=True)
                known = Decimal(str(state["total"])) if state else (cached[1] if cached else None)
                return known
            try:
                _save(path, {"total": str(total), "cursor": now_ms})
            except OSError as exc:
                print(f"Transfer total not saved: {exc}", flush=True)
            BinanceFuturesMarket._deposits_cache = (now, total)
            return total

    @classmethod
    def invalidate_pilot_cache(cls):
        """Called after every fill (live_short_controller._opened), so the
        other system's next decision sees the new position at once."""
        with cls._raw_pilot_lock:
            cls._raw_pilot_cache = None

    def pilot_status(self):
        account, open_orders, orders = self._raw_pilot_data()
        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0,
                                                   microsecond=0).timestamp() * 1000
        return summarize_short_pilot(account, open_orders, orders, self.config, int(start), self.tag,
                                     net_deposits=self.net_deposits())

    def live_positions(self):
        # Recognizes both a short's protective stop (kv1fp, BUY -- buys
        # back to close) and a long's (kv1fq, SELL -- sells to close), 21
        # Sep 2026, tagging each returned position with its own side so
        # the caller (render_web.LiveApp.scan) can apply the right exit
        # logic and executor calls to each.
        positions = []
        # One position per symbol and side. A stop move places the new stop
        # before cancelling the old one, so for a moment -- or longer, if that
        # cancel failed -- two can rest: the tighter one is the position's
        # stop, the others are returned as extra_stop_ids for the scan to
        # cancel (they are reduce-only, so harmless meanwhile).
        stops = {}
        for stop in self.executor.open_orders():
            stop_id = str(stop.get("clientOrderId", ""))
            if stop_id.startswith(_SHORT_STOP_PREFIX) and stop.get("side") == "BUY":
                side = "short"
            elif stop_id.startswith(_LONG_STOP_PREFIX) and stop.get("side") == "SELL":
                side = "long"
            else:
                continue
            if not _belongs_to_tag(stop_id, 5, self.tag):
                continue
            stops.setdefault((stop["symbol"], side), []).append(stop)
        account, _, _ = self._raw_pilot_data()
        # What is really open, per symbol. A stop's own quantity goes stale
        # once part of the position is closed (by hand, or a take-profit)
        # and every moved stop copied it (audit A6).
        amounts = {p.get("symbol"): abs(_decimal(p.get("positionAmt"))) for p in account.get("positions", [])
                   if abs(_decimal(p.get("positionAmt"))) > 0}
        held = set(amounts)
        # Stops whose position is gone (closed by hand, or by an exit whose
        # stop cancel failed). They are not positions: until 24 Sep 2026 one
        # still counted as a held symbol and an open slot. The scan re-checks
        # each live -- the account read above can be up to 90 s old -- before
        # cancelling it.
        self.orphan_stops = []
        for (symbol, side), group in stops.items():
            group.sort(key=lambda o: _decimal(o.get("stopPrice")), reverse=(side == "long"))
            stop, extras = group[0], group[1:]
            stop_id = str(stop.get("clientOrderId", ""))
            if symbol not in held:
                self.orphan_stops.append({"symbol": symbol, "side": side,
                                          "stop_ids": [str(o.get("clientOrderId", "")) for o in group]})
                continue
            open_prefix = _LONG_OPEN_PREFIX if side == "long" else _SHORT_OPEN_PREFIX
            try:
                open_order = self.executor.query(stop["symbol"], open_prefix + base_suffix(stop_id))
            except OrderRejected as error:
                if error.code != -2013:  # Binance: order does not exist.
                    raise
                open_order = None
            if open_order is not None:
                qty = Decimal(str(open_order.get("executedQty", "0")))
                quote = Decimal(str(open_order.get("cumQuote", "0")))
            if open_order is None or qty <= 0 or quote <= 0:
                # An adopted position's stop carries a per-symbol code its
                # older opening order does not, so that lookup 404s (-2013).
                # The exchange's own position is the authority anyway; only
                # falling back here keeps one missing order from aborting the
                # WHOLE exit scan, which is what happened live 23 Sep 2026
                # ("Futures exit scan failed: ... -2013" every tick, so no
                # position was being checked for its exit at all).
                account_position = self._account_position(stop["symbol"])
                if account_position is None:
                    continue
                entry, quantity, open_time = account_position
            else:
                entry, quantity = quote / qty, format(amounts[symbol], "f")
                open_time = int(open_order.get("time", open_order.get("updateTime", 0)))
            positions.append({"symbol": stop["symbol"], "side": side, "entry": entry,
                              "stop_price": Decimal(str(stop["stopPrice"])),
                              "quantity": quantity, "stop_client_id": stop_id,
                              "open_time": open_time,
                              "extra_stop_ids": [str(o.get("clientOrderId", "")) for o in extras]})
        positions.extend(self.unprotected_positions({p["symbol"] for p in positions}))
        return positions

    def _account_position(self, symbol):
        """(entry, quantity, open_time) straight from the exchange, or None
        when nothing is open. open_time falls back to now: analysis() only
        uses it to decide how far back to fetch candles, and a zero there
        would ask Binance for every candle since 1970."""
        account, _, _ = self._raw_pilot_data()
        for position in account.get("positions", []):
            amount = _decimal(position.get("positionAmt"))
            if position.get("symbol") != symbol or amount == 0:
                continue
            open_time = int(position.get("updateTime") or 0) or int(self._now() * 1000)
            return _decimal(position.get("entryPrice")), format(abs(amount), "f"), open_time
        return None

    def unprotected_positions(self, protected_symbols=()):
        """Real Futures positions this system opened that have NO protective
        stop resting on Binance. Until 22 Sep 2026 such a position was
        simply invisible (live_positions only ever saw a position through
        its stop order), so when every stop placement started failing with
        -4120 six real positions went unmanaged and unprotected overnight.
        Attribution is by the opening order's own tag, so the 4H and 2H
        systems never both adopt the same position; a position with no
        opening order of ours (opened by hand, or older than the order
        history window) is deliberately left alone.

        Ownership is proven, not assumed from the symbol (audit A4): the
        bot's opening order, less every later fill in the symbol, must add
        up to exactly what is open now. Until 25 Sep 2026 any position in a
        symbol the bot had bought within 7 days was taken over -- a BNB the
        user bought by hand after the bot's BNB trade had ended would get
        the bot's stop and exits."""
        account, _, orders = self._raw_pilot_data()
        opens, fills = {}, {}
        for order in orders:
            client_id = str(order.get("clientOrderId", ""))
            if _decimal(order.get("executedQty")) > 0:  # a cancelled order can still have part-filled
                fills.setdefault(order.get("symbol"), []).append(order)
            if order.get("status") != "FILLED" or not client_id.startswith((_LONG_OPEN_PREFIX, _SHORT_OPEN_PREFIX)):
                continue
            seen = opens.get(order["symbol"])
            if seen is None or int(order.get("time", 0)) >= int(seen.get("time", 0)):
                opens[order["symbol"]] = order
        positions = []
        for position in account.get("positions", []):
            amount = _decimal(position.get("positionAmt"))
            if amount == 0 or position["symbol"] in set(protected_symbols):
                continue
            opened = opens.get(position["symbol"])
            if opened is None or not _belongs_to_tag(opened["clientOrderId"], 5, self.tag):
                continue
            if _left_open(opened, fills.get(position["symbol"], [])) != amount:
                self._note_foreign(position["symbol"], amount)
                continue
            client_id = str(opened["clientOrderId"])
            side = "long" if amount > 0 else "short"
            stop_prefix = _LONG_STOP_PREFIX if side == "long" else _SHORT_STOP_PREFIX
            # Per-symbol id even when the opening order predates symbol_code
            # (its suffix may be shared with another symbol's position).
            suffix = client_id[5:]
            if not suffix.startswith(str(self.tag) + symbol_code(position["symbol"])):
                suffix = str(self.tag) + symbol_code(position["symbol"]) + suffix[len(str(self.tag)):]
            positions.append({"symbol": position["symbol"], "side": side,
                              "entry": _decimal(position.get("entryPrice")),
                              "stop_price": None, "unprotected": True,
                              "quantity": format(abs(amount), "f"),
                              "stop_client_id": stop_prefix + suffix,
                              "open_time": int(opened.get("time") or opened.get("updateTime") or 0)})
        return positions

    def _note_foreign(self, symbol, amount):
        """One Telegram line per position the bot will not touch although it
        traded that coin: opened by hand after the bot's own trade ended,
        or changed by hand since. It has no stop from the bot."""
        key = (symbol, str(amount))
        if key in BinanceFuturesMarket._foreign_noted:
            return
        BinanceFuturesMarket._foreign_noted.add(key)
        print(f"Position not adopted ({symbol} {amount}): not the bot's own", flush=True)
        try:
            send_message(f"ELLE ACILMIS POZISYON: {symbol} ({format(amount, 'f')}) botun kendi islemiyle "
                         "eslesmiyor. Dokunmuyorum, stop koymuyorum; korumasi sizde.")
        except Exception as exc:
            print(f"Telegram foreign-position notice failed: {exc}", flush=True)

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
