"""The dip-catcher with REAL orders (user's decision, 30 Sep 2026: "canliya al").

Same rules as the paper version (crypto_v1.dip_catcher), on Binance USD-M
Futures, run from the loop's housekeeping step (a failure never touches the
trend systems):
  * each UTC day: cancel yesterday's unfilled buys, then rest a conditional
    market buy (kv1fd, TAKE_PROFIT_MARKET BUY: fires when the last price
    falls to the level) on each of the day's top-N coins not already held,
    at yesterday's daily close x (1 - depth), 3x isolated leverage so the
    liquidation price stays under the -25% stop, sized at size_fraction of
    the 2H sleeve. Not a limit order: Binance refuses a limit buy more than
    5-15% under the mark (PERCENT_PRICE), checked 30 Sep 2026;
  * a fill gets a reduce-only STOP_MARKET (kv1fz) at -25% and
    TAKE_PROFIT_MARKET (kv1fg) at +8% right away; a stop that cannot be
    placed closes the position at market (kv1fw);
  * once one of them fires the other is cancelled; after hold_hours both are
    cancelled and the position is sold at market (kv1fw);
  * a resting buy on a coin a trend system has just bought is cancelled
    (one-way account), and the trend systems will not buy a coin while
    kv1fg/kv1fz rest on it (live_short_market.summarize_short_pilot).
Every order carries a deterministic client id (prefix + symbol code + day),
so an unknown order result is settled by querying it, never by sending it
twice."""
import time
from decimal import Decimal

from .binance_trade import OrderRejected, OrderStateUnknown
from .dip_catcher import DAY_MS, _public_klines, _read, _write
from .live_execution import MIN_NOTIONAL_HEADROOM, _down, _up
from .live_short_market import symbol_code

ZERO = Decimal("0")


class LiveDipCatcher:
    LEVERAGE = 3

    def __init__(self, config, state_path, manifest_path, executor, market, send=None, clock=time.time,
                 equity_fn=None, klines=None):
        self.config = dict(config or {})
        self.state_path, self.manifest_path = state_path, manifest_path
        self.executor, self.market = executor, market
        if send is None:
            from .telegram import send_message as send
        self.send, self.clock, self.equity_fn = send, clock, equity_fn
        self.klines = klines or _public_klines

    @property
    def enabled(self):
        return self.config.get("mode") == "live"

    def _say(self, text):
        try:
            self.send("[IGNE] " + text)
        except Exception as exc:
            print(f"Dip-catcher Telegram failed: {exc}", flush=True)

    @staticmethod
    def _id(prefix, symbol, day):
        return f"{prefix}{symbol_code(symbol)}{int(day)}"

    def tick(self):
        if not self.enabled:
            return None
        now_ms = int(self.clock() * 1000)
        state = _read(self.state_path, {}) or {}
        for key, default in (("orders", {}), ("positions", {}), ("closed", [])):
            state.setdefault(key, default)
        account = self.executor.account()
        amounts = {p["symbol"]: Decimal(str(p.get("positionAmt") or "0")) for p in account.get("positions", [])}
        self.entries = {p["symbol"]: Decimal(str(p.get("entryPrice") or "0")) for p in account.get("positions", [])}
        open_ids = {str(o.get("clientOrderId", "")) for o in self.executor.open_orders()}
        self._reconcile_orders(state, amounts, open_ids)
        day = now_ms // DAY_MS * DAY_MS
        if state.get("day") != day:
            self._expire_orders(state)
            state["day"] = day
            _write(self.state_path, state)
            self._place_orders(state, day, now_ms, amounts)
        self._manage_positions(state, amounts, open_ids, now_ms)
        _write(self.state_path, state)
        return state

    # ---- resting buys ------------------------------------------------------------------------------
    def _reconcile_orders(self, state, amounts, open_ids):
        for symbol, order in list(state["orders"].items()):
            if order["id"] in open_ids:
                if amounts.get(symbol, ZERO) != 0 and symbol not in state["positions"]:
                    self._cancel_order(state, symbol, order, "baska sistem aldi")
                continue
            self._settle_order(state, symbol, order, amounts)

    def _settle_order(self, state, symbol, order, amounts=None):
        """The buy is no longer resting (or was just cancelled). If it fired, what it bought is
        the account's long in that coin: no other system holds it (the trend systems skip coins
        where our take-profit/stop rest, and a resting buy is cancelled when they buy) -- so the
        position is protected as ours."""
        try:
            info = self.executor.query(symbol, order["id"])
        except OrderRejected as error:
            if error.code == -2013:        # never reached Binance
                state["orders"].pop(symbol, None)
                return
            raise
        status = info.get("status")
        if status == "NEW":
            return                          # still resting (the open-orders list lagged)
        state["orders"].pop(symbol, None)
        held = (amounts or {}).get(symbol, ZERO)
        if status == "FILLED" and held > 0 and symbol not in state["positions"]:
            entry = getattr(self, "entries", {}).get(symbol) or Decimal(order["price"])
            # Never more than we bought: if a trend buy slipped in first (one-way account), our
            # stop/take-profit/time exit must only ever close our own part.
            qty = min(held, Decimal(order["qty"]))
            self._protect(state, symbol, qty, entry, int(info.get("updateTime") or self.clock() * 1000))

    def _cancel_order(self, state, symbol, order, why):
        try:
            self.executor.cancel(symbol, order["id"])
        except OrderRejected as error:
            if error.code not in (-2011, -2013):   # already filled / cancelled / unknown: settle below
                print(f"Dip-catcher cancel failed ({symbol}): {error}", flush=True)
                return
        except Exception as exc:
            print(f"Dip-catcher cancel failed ({symbol}): {exc}", flush=True)
            return
        self._settle_order(state, symbol, order, self._amounts_now())
        state["orders"].pop(symbol, None)
        print(f"Dip-catcher order cancelled ({symbol}): {why}", flush=True)

    def _amounts_now(self):
        account = self.executor.account()
        self.entries = {p["symbol"]: Decimal(str(p.get("entryPrice") or "0")) for p in account.get("positions", [])}
        return {p["symbol"]: Decimal(str(p.get("positionAmt") or "0")) for p in account.get("positions", [])}

    def _expire_orders(self, state):
        for symbol, order in list(state["orders"].items()):
            self._cancel_order(state, symbol, order, "gun bitti")

    def _place_orders(self, state, day, now_ms, amounts):
        top_n = int(self.config.get("top_n", 10))
        depth = Decimal(str(self.config.get("depth", 0.15)))
        ranked = [s for s in (_read(self.manifest_path, {}) or {}).get("symbols", []) if s != "BTCUSDT"][:top_n]
        try:
            equity = Decimal(str(self.equity_fn())) if self.equity_fn else None
        except Exception as exc:
            print(f"Dip-catcher equity unreadable, no orders today: {exc}", flush=True)
            return
        if not equity or equity <= 0:
            return
        size = equity * Decimal(str(self.config.get("size_fraction", 0.10)))
        placed, skipped = [], []
        for symbol in ranked:
            if symbol in state["positions"] or symbol in state["orders"] or amounts.get(symbol, ZERO) != 0:
                continue
            try:
                daily = self.klines(symbol, "1d", None, 3)
                prev = [r for r in daily if int(r[0]) == day - DAY_MS and int(r[6]) < now_ms]
                if not prev:
                    continue
                rules = self.market.rules(symbol)
                price = _down(Decimal(str(prev[0][4])) * (1 - depth), rules["tick_size"])
                qty = _down(size / price, rules["step_size"])
                if qty < rules["min_qty"] or qty * price < rules["min_notional"] * MIN_NOTIONAL_HEADROOM:
                    skipped.append(symbol.removesuffix("USDT"))
                    continue
                self.executor.set_isolated_margin(symbol)
                self.executor.set_leverage(symbol, self.LEVERAGE)
            except Exception as exc:
                print(f"Dip-catcher could not prepare {symbol}: {exc}", flush=True)
                continue
            order = {"id": self._id("kv1fd", symbol, day), "price": format(price, "f"), "qty": format(qty, "f")}
            state["orders"][symbol] = order        # recorded first: an unknown result is settled by query
            _write(self.state_path, state)
            try:
                self.executor.triggered_open_long(symbol, order["qty"], order["price"], order["id"])
            except OrderStateUnknown:
                pass
            except Exception as exc:
                state["orders"].pop(symbol, None)
                print(f"Dip-catcher order rejected ({symbol}): {exc}", flush=True)
                continue
            placed.append(f"{symbol.removesuffix('USDT')} {order['price']}")
        if placed:
            text = (f"Bugunun bekleyen alim emirleri (dunku kapanisin %{depth * 100:.0f} alti, her biri "
                    f"~{size:.2f} USDT, {self.LEVERAGE}x): " + ", ".join(placed) + ".")
            if skipped:
                text += " Binance minimumu yuzunden atlanan: " + ", ".join(skipped) + "."
            self._say(text)

    # ---- positions ---------------------------------------------------------------------------------
    def _protect(self, state, symbol, qty, entry, filled_ms):
        day = state.get("day") or filled_ms // DAY_MS * DAY_MS
        rules = self.market.rules(symbol)
        stop = _down(entry * (1 - Decimal(str(self.config.get("stop", 0.25)))), rules["tick_size"])
        tp = _up(entry * (1 + Decimal(str(self.config.get("take_profit", 0.08)))), rules["tick_size"])
        hold_ms = int(float(self.config.get("hold_hours", 24)) * 3_600_000)
        pos = {"qty": format(qty, "f"), "entry": format(entry, "f"), "stop": format(stop, "f"), "tp": format(tp, "f"),
               "stop_id": self._id("kv1fz", symbol, day), "tp_id": self._id("kv1fg", symbol, day),
               "exit_id": self._id("kv1fw", symbol, day), "filled_at": filled_ms, "until": filled_ms + hold_ms}
        state["positions"][symbol] = pos
        _write(self.state_path, state)
        try:
            self.executor.protective_stop_for_long(symbol, pos["qty"], pos["stop"], pos["stop_id"])
        except Exception as exc:
            print(f"Dip-catcher stop failed ({symbol}): {exc}; closing at market", flush=True)
            self._market_exit(state, symbol, "acil kapatma (stop konamadi)")
            return
        try:
            self.executor.take_profit_for_long(symbol, pos["qty"], pos["tp"], pos["tp_id"])
        except Exception as exc:
            print(f"Dip-catcher take-profit failed ({symbol}): {exc}", flush=True)
        self._say(f"ALDIM: {symbol.removesuffix('USDT')} {pos['qty']} @ {pos['entry']} | hedef {pos['tp']} "
                  f"(+%{float(self.config.get('take_profit', 0.08)) * 100:.0f}), stop {pos['stop']} "
                  f"(-%{float(self.config.get('stop', 0.25)) * 100:.0f}), en gec {hold_ms // 3_600_000} saat.")

    def _manage_positions(self, state, amounts, open_ids, now_ms):
        for symbol, pos in list(state["positions"].items()):
            if amounts.get(symbol, ZERO) <= 0:
                tp_open, stop_open = pos["tp_id"] in open_ids, pos["stop_id"] in open_ids
                # the one that is gone is the one that fired
                reason, price = ("stop", pos["stop"]) if tp_open and not stop_open else ("hedef", pos["tp"])
                for cid in (pos["tp_id"], pos["stop_id"]):
                    if cid in open_ids:
                        self._quiet_cancel(symbol, cid)
                self._record(state, symbol, Decimal(price), reason, now_ms)
            elif now_ms >= pos["until"]:
                self._market_exit(state, symbol, "sure doldu")

    def _quiet_cancel(self, symbol, client_id):
        try:
            self.executor.cancel(symbol, client_id)
        except Exception as exc:
            print(f"Dip-catcher cancel failed ({symbol} {client_id}): {exc}", flush=True)

    def _market_exit(self, state, symbol, reason):
        pos = state["positions"][symbol]
        for cid in (pos["tp_id"], pos["stop_id"]):
            self._quiet_cancel(symbol, cid)
        try:
            result = self.executor.market_close_long(symbol, pos["qty"], pos["exit_id"])
        except OrderStateUnknown:
            result = self.executor.query(symbol, pos["exit_id"])
        price = Decimal(str(result.get("avgPrice") or "0")) or Decimal(pos["entry"])
        self._record(state, symbol, price, reason, int(self.clock() * 1000))

    def _record(self, state, symbol, price, reason, at_ms):
        pos = state["positions"].pop(symbol)
        qty, entry = Decimal(pos["qty"]), Decimal(pos["entry"])
        pnl = float(qty * (price - entry))
        pct = float(price / entry - 1)
        state["closed"].append({"symbol": symbol, "entry": float(entry), "exit": float(price), "pnl": pnl,
                                "pct": pct, "reason": reason, "filled_at": pos["filled_at"], "closed_at": at_ms})
        wins = sum(1 for r in state["closed"] if r["pnl"] > 0)
        total = sum(r["pnl"] for r in state["closed"])
        self._say(f"KAPANDI ({reason}): {symbol.removesuffix('USDT')} {pos['entry']} -> {price} | {pct * 100:+.1f}% "
                  f"(yaklasik {pnl:+.2f} USDT, komisyon haric) | igne toplam: {len(state['closed'])} islem, "
                  f"{wins} kazanan, {total:+.2f} USDT")
