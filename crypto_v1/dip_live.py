"""The dip-catcher with real orders, version 2 (user's decision, 30 Sep 2026).

Version 1 rested conditional buys 15% under yesterday's close on the top-10
coins. The user pointed out that the top coins all falling 15% at once is a
market crash, where every order would fill together and keep falling. The
5-minute test (research/pit/frequent_dip_5m.py) agreed: only buying when
the market itself is calm was consistent in both 12-month eras.

Version 2, checked on every loop (~2 minutes) from the housekeeping step:
  * each UTC day: watch the day's top-N coins at yesterday's close x
    (1 - depth); nothing rests on Binance;
  * a watched coin at or under its level is bought at market (kv1fc) ONLY
    while BTC's lowest price so far today is at most btc_calm under its own
    yesterday close, at most once per coin per day, and not when the
    account holds the coin or another of our orders rests on it;
  * the buy gets a reduce-only STOP_MARKET (kv1fz) and TAKE_PROFIT_MARKET
    (kv1fg) right away (a stop that cannot be placed closes it at market);
    once one fires the other is cancelled; after hold_hours both are
    cancelled and it is sold at market (kv1fw);
  * 3x isolated, so liquidation stays under the stop; sized at
    size_fraction of the 2H sleeve (notional).
Version 1's resting buys (kv1fd) are cancelled on the first tick. Every
order carries a deterministic client id (prefix + symbol code + day)."""
import time
from decimal import Decimal

from .binance_trade import OrderRejected, OrderStateUnknown
from .dip_catcher import DAY_MS, _public_klines, _read, _write
from .live_execution import MIN_NOTIONAL_HEADROOM, _down, _up
from .live_short_market import symbol_code
from .side_methods import settle_buy

ZERO = Decimal("0")


class LiveDipCatcher:
    LEVERAGE = 3
    settle_sleep = staticmethod(time.sleep)

    def __init__(self, config, state_path, manifest_path, executor, market, send=None, clock=time.time,
                 equity_fn=None, klines=None, price_fn=None):
        self.config = dict(config or {})
        self.state_path, self.manifest_path = state_path, manifest_path
        self.executor, self.market = executor, market
        if send is None:
            from .telegram import send_message as send
        self.send, self.clock, self.equity_fn = send, clock, equity_fn
        self.klines = klines or _public_klines
        self.price_fn = price_fn or (lambda symbol: market.price(symbol))

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
        for key, default in (("orders", {}), ("positions", {}), ("closed", []), ("watch", {}), ("bought", [])):
            state.setdefault(key, default)
        account = self.executor.account()
        amounts = {p["symbol"]: Decimal(str(p.get("positionAmt") or "0")) for p in account.get("positions", [])}
        self.entries = {p["symbol"]: Decimal(str(p.get("entryPrice") or "0")) for p in account.get("positions", [])}
        open_orders = self.executor.open_orders()
        open_ids = {str(o.get("clientOrderId", "")) for o in open_orders}
        busy = {o.get("symbol") for o in open_orders if str(o.get("clientOrderId", "")).startswith("kv1f")}
        self._cancel_legacy(state, amounts)
        day = now_ms // DAY_MS * DAY_MS
        if state.get("day") != day:
            state["day"], state["bought"] = day, []
            state["watch"], state["btc_prev"] = self._levels(day, now_ms)
            _write(self.state_path, state)
        self._manage_positions(state, amounts, open_ids, now_ms)
        self._check_watch(state, amounts, busy, day, now_ms)
        _write(self.state_path, state)
        return state

    # ---- version 1 leftovers ------------------------------------------------------------------------
    def _cancel_legacy(self, state, amounts):
        """Version 1's resting conditional buys: cancel, and protect anything one already bought."""
        for symbol, order in list(state["orders"].items()):
            try:
                self.executor.cancel(symbol, order["id"])
            except OrderRejected as error:
                if error.code not in (-2011, -2013):
                    print(f"Dip-catcher legacy cancel failed ({symbol}): {error}", flush=True)
                    continue
            except Exception as exc:
                print(f"Dip-catcher legacy cancel failed ({symbol}): {exc}", flush=True)
                continue
            try:
                info = self.executor.query(symbol, order["id"])
            except OrderRejected:
                info = {}
            if info.get("status") == "FILLED" and amounts.get(symbol, ZERO) > 0 and symbol not in state["positions"]:
                qty = min(amounts[symbol], Decimal(order["qty"]))
                entry = self.entries.get(symbol) or Decimal(order["price"])
                self._protect(state, symbol, qty, entry, int(self.clock() * 1000))
            state["orders"].pop(symbol, None)
            print(f"Dip-catcher v1 order cancelled: {symbol}", flush=True)

    # ---- watching ----------------------------------------------------------------------------------
    def _prev_close(self, symbol, day, now_ms):
        daily = self.klines(symbol, "1d", None, 3)
        prev = [r for r in daily if int(r[0]) == day - DAY_MS and int(r[6]) < now_ms]
        return Decimal(str(prev[0][4])) if prev else None

    def _levels(self, day, now_ms):
        top_n = int(self.config.get("top_n", 10))
        depth = Decimal(str(self.config.get("depth", 0.10)))
        ranked = [s for s in (_read(self.manifest_path, {}) or {}).get("symbols", []) if s != "BTCUSDT"][:top_n]
        watch, shown = {}, []
        for symbol in ranked:
            try:
                close = self._prev_close(symbol, day, now_ms)
            except Exception as exc:
                print(f"Dip-catcher daily close unreadable ({symbol}): {exc}", flush=True)
                continue
            if close:
                watch[symbol] = format(close * (1 - depth), "f")
                shown.append(f"{symbol.removesuffix('USDT')} {float(close * (1 - depth)):.6g}")
        try:
            btc = self._prev_close("BTCUSDT", day, now_ms)
        except Exception as exc:
            print(f"Dip-catcher BTC close unreadable: {exc}", flush=True)
            btc = None
        if shown:
            calm = float(self.config.get("btc_calm", 0.02)) * 100
            self._say(f"Bugun izlenen igne seviyeleri (dunku kapanisin %{depth * 100:.0f} alti; sadece BTC bugun "
                      f"%{calm:.0f}'den fazla dusmemisse alinir): " + ", ".join(shown) + ".")
        return watch, (format(btc, "f") if btc else None)

    def _btc_calm(self, state, day, now_ms):
        if not state.get("btc_prev"):
            return False
        rows = self.klines("BTCUSDT", "5m", day, 300)
        lows = [Decimal(str(r[3])) for r in rows if int(r[0]) >= day]
        if not lows:
            return False
        drop = 1 - min(lows) / Decimal(state["btc_prev"])
        return drop <= Decimal(str(self.config.get("btc_calm", 0.02)))

    def _check_watch(self, state, amounts, busy, day, now_ms):
        todo = [s for s in state["watch"] if s not in state["bought"] and s not in state["positions"]
                and amounts.get(s, ZERO) == 0 and s not in busy]
        if not todo:
            return
        hits = []
        for symbol in todo:
            try:
                price = Decimal(str(self.price_fn(symbol)))
            except Exception as exc:
                print(f"Dip-catcher price unreadable ({symbol}): {exc}", flush=True)
                continue
            if price <= Decimal(state["watch"][symbol]):
                hits.append((symbol, price))
        if not hits:
            return
        try:
            calm = self._btc_calm(state, day, now_ms)
        except Exception as exc:
            print(f"Dip-catcher BTC check failed, no buy: {exc}", flush=True)
            return
        if not calm:
            for symbol, _ in hits:
                if symbol not in state.setdefault("told_btc", []):
                    state["told_btc"].append(symbol)
                    self._say(f"{symbol.removesuffix('USDT')} seviyeye indi ama BTC de bugun sert dustu; alinmadi.")
            return
        for symbol, price in hits:
            self._buy(state, symbol, price, day)

    def _buy(self, state, symbol, price, day):
        try:
            equity = Decimal(str(self.equity_fn()))
            rules = self.market.rules(symbol)
            size = equity * Decimal(str(self.config.get("size_fraction", 0.10)))
            qty = _down(size / price, rules["step_size"])
            if qty < rules["min_qty"] or qty * price < rules["min_notional"] * MIN_NOTIONAL_HEADROOM:
                state["bought"].append(symbol)
                self._say(f"{symbol.removesuffix('USDT')} seviyeye indi ama tutar Binance minimumunun altinda; atlandi.")
                return
            self.executor.set_isolated_margin(symbol)
            self.executor.set_leverage(symbol, self.LEVERAGE)
        except Exception as exc:
            print(f"Dip-catcher could not prepare {symbol}: {exc}", flush=True)
            return
        buy_id = self._id("kv1fc", symbol, day)
        state["bought"].append(symbol)          # once per coin per day, even if the result is unknown
        _write(self.state_path, state)
        try:
            result = self.executor.market_open_long(symbol, format(qty, "f"), buy_id)
        except OrderStateUnknown:
            result = self.executor.query(symbol, buy_id)
        except Exception as exc:
            print(f"Dip-catcher buy rejected ({symbol}): {exc}", flush=True)
            return
        filled, avg = settle_buy(self.executor, symbol, buy_id, result, sleep=self.settle_sleep)
        if filled > 0:
            self._protect(state, symbol, filled, avg or price, int(self.clock() * 1000))
        else:
            self._say(f"{symbol.removesuffix('USDT')} alim emri gonderildi ama dolmus gorunmuyor; "
                      f"Binance'te kontrol edin.")

    # ---- positions ---------------------------------------------------------------------------------
    def _protect(self, state, symbol, qty, entry, filled_ms):
        day = state.get("day") or filled_ms // DAY_MS * DAY_MS
        rules = self.market.rules(symbol)
        stop = _down(entry * (1 - Decimal(str(self.config.get("stop", 0.10)))), rules["tick_size"])
        tp = _up(entry * (1 + Decimal(str(self.config.get("take_profit", 0.03)))), rules["tick_size"])
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
                  f"(+%{float(self.config.get('take_profit', 0.03)) * 100:.0f}), stop {pos['stop']} "
                  f"(-%{float(self.config.get('stop', 0.10)) * 100:.0f}), en gec {hold_ms // 3_600_000} saat.")

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
