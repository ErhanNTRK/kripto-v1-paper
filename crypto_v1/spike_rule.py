"""The user's big-candle-then-red rule ("buyuk mum + kirmizi mum"), live (30 Sep 2026).

Detection (on each closed 2H bar, from the 2H scan's own candles): the
previous bar is a green candle with a body of at least min_body_atr ATR whose
high is the highest high of the 50 bars before it; the last closed bar is
red; BTC's 2H close is above its EMA50 and the coin's Ichimoku is fully
bullish on that red bar. Execution (housekeeping step, after the trend
systems had their turn on the same bar): a market buy (kv1fh) sized at
risk_fraction of the 2H sleeve against a stop stop_atr ATR under the red
close, a reduce-only STOP_MARKET there (kv1fj; if it cannot be placed the
position is closed at market), and a market sale after hold_hours (kv1fv).
A coin the account already holds, or where another of our orders rests,
is skipped; the trend systems skip coins while kv1fj rests.

Research (research/pit/spike_pullback.py, spike_portfolio.py): inside the
2H sleeve it lifted a typical 12 months per 100 USD from 91 to 120 in
2021-23 and from 205 to 229 in 2023-26."""
import time
from decimal import Decimal

from .dip_catcher import _read, _write
from .side_methods import settle_buy

TWO_HOUR_MS = 2 * 3_600_000


def spike_candidates(data, symbols, config, min_body_atr=3.0, lookback=50):
    """data: {symbol: closed 2H rows (oldest first)}, BTCUSDT included."""
    from .research_v5 import symmetric_features
    btc_rows = data.get("BTCUSDT") or []
    if len(btc_rows) < 60:
        return []
    btc = symmetric_features(btc_rows, config)[-1]
    if not btc.get("ema50") or btc["c"] <= btc["ema50"]:
        return []
    found = []
    for symbol in symbols:
        rows = data.get(symbol) or []
        if len(rows) < lookback + 60:
            continue
        feats = symmetric_features(rows, config)
        big, red = feats[-2], feats[-1]
        atr = big.get("atr")
        if not atr or red["t"] != btc["t"]:
            continue
        if big["c"] - big["o"] < min_body_atr * atr or red["c"] >= red["o"]:
            continue
        if big["h"] < max(f["h"] for f in feats[-2 - lookback:-2]):
            continue
        if red.get("ichimoku_ok") is not True:
            continue
        found.append({"symbol": symbol, "close": red["c"], "atr": atr, "time": red["t"]})
    return found


class LiveSpikeRule:
    LEVERAGE = 3
    settle_sleep = staticmethod(time.sleep)

    def __init__(self, config, state_path, signal_path, executor, market, send=None, clock=time.time,
                 equity_fn=None):
        self.config = dict(config or {})
        self.state_path, self.signal_path = state_path, signal_path
        self.executor, self.market, self.equity_fn = executor, market, equity_fn
        if send is None:
            from .telegram import send_message as send
        self.send, self.clock = send, clock

    @property
    def enabled(self):
        return self.config.get("mode") == "live"

    def _say(self, text):
        try:
            self.send("[MUM] " + text)
        except Exception as exc:
            print(f"Spike rule Telegram failed: {exc}", flush=True)

    @staticmethod
    def _id(prefix, symbol, signal_time):
        from .live_short_market import symbol_code
        return f"{prefix}{symbol_code(symbol)}{int(signal_time)}"

    def tick(self):
        if not self.enabled:
            return None
        now_ms = int(self.clock() * 1000)
        state = _read(self.state_path, {}) or {}
        state.setdefault("positions", {}); state.setdefault("closed", []); state.setdefault("seen", [])
        account = self.executor.account()
        amounts = {p["symbol"]: Decimal(str(p.get("positionAmt") or "0")) for p in account.get("positions", [])}
        entries = {p["symbol"]: Decimal(str(p.get("entryPrice") or "0")) for p in account.get("positions", [])}
        open_orders = self.executor.open_orders()
        open_ids = {str(o.get("clientOrderId", "")) for o in open_orders}
        busy = {o.get("symbol") for o in open_orders if str(o.get("clientOrderId", "")).startswith("kv1f")}
        self._manage(state, amounts, open_ids, now_ms)
        self._enter(state, amounts, entries, busy, now_ms)
        _write(self.state_path, state)
        return state

    def _enter(self, state, amounts, entries, busy, now_ms):
        from .binance_trade import OrderStateUnknown
        from .live_execution import MIN_NOTIONAL_HEADROOM, _down
        signals = _read(self.signal_path, {}) or {}
        max_age = int(self.config.get("max_age_minutes", 30)) * 60_000
        for c in signals.get("candidates", []):
            key = f"{c['symbol']}:{c['time']}"
            if key in state["seen"]:
                continue
            state["seen"] = (state["seen"] + [key])[-200:]
            symbol = c["symbol"]
            bar_close = int(c["time"]) + TWO_HOUR_MS
            if now_ms - bar_close > max_age:
                print(f"Spike signal too old, skipped: {symbol}", flush=True)
                continue
            if symbol in state["positions"] or amounts.get(symbol, Decimal("0")) != 0 or symbol in busy:
                print(f"Spike signal skipped, coin busy: {symbol}", flush=True)
                continue
            try:
                equity = Decimal(str(self.equity_fn()))
                rules = self.market.rules(symbol)
                price = Decimal(str(self.market.price(symbol)))
                stop = _down(Decimal(str(c["close"])) - Decimal(str(self.config.get("stop_atr", 2.0))) * Decimal(str(c["atr"])),
                             rules["tick_size"])
                if stop <= 0 or price <= stop:
                    continue
                risk = equity * Decimal(str(self.config.get("risk_fraction", 0.015)))
                qty = _down(min(risk / (price - stop), equity * self.LEVERAGE / price), rules["step_size"])
                if qty < rules["min_qty"] or qty * price < rules["min_notional"] * MIN_NOTIONAL_HEADROOM:
                    self._say(f"{symbol.removesuffix('USDT')} sinyali geldi ama tutar Binance minimumunun altinda; atlandi.")
                    continue
                self.executor.set_isolated_margin(symbol)
                self.executor.set_leverage(symbol, self.LEVERAGE)
            except Exception as exc:
                print(f"Spike rule could not prepare {symbol}: {exc}", flush=True)
                continue
            ids = {k: self._id(p, symbol, c["time"]) for k, p in (("open", "kv1fh"), ("stop", "kv1fj"), ("exit", "kv1fv"))}
            try:
                result = self.executor.market_open_long(symbol, format(qty, "f"), ids["open"])
            except OrderStateUnknown:
                result = self.executor.query(symbol, ids["open"])
            except Exception as exc:
                print(f"Spike rule buy rejected ({symbol}): {exc}", flush=True)
                continue
            filled, avg = settle_buy(self.executor, symbol, ids["open"], result, sleep=self.settle_sleep)
            if filled <= 0:
                self._say(f"{symbol.removesuffix('USDT')} alim emri gonderildi ama dolmus gorunmuyor; "
                          f"Binance'te kontrol edin.")
                continue
            entry = avg or price
            hold_ms = int(float(self.config.get("hold_hours", 24)) * 3_600_000)
            pos = {"qty": format(filled, "f"), "entry": format(entry, "f"), "stop": format(stop, "f"),
                   "stop_id": ids["stop"], "exit_id": ids["exit"], "opened_at": now_ms, "until": bar_close + hold_ms}
            state["positions"][symbol] = pos
            _write(self.state_path, state)
            try:
                self.executor.protective_stop_for_long(symbol, pos["qty"], pos["stop"], pos["stop_id"])
            except Exception as exc:
                print(f"Spike rule stop failed ({symbol}): {exc}; closing at market", flush=True)
                self._exit(state, symbol, "acil kapatma (stop konamadi)")
                continue
            self._say(f"ALDIM: {symbol.removesuffix('USDT')} {pos['qty']} @ {pos['entry']} | stop {pos['stop']} | "
                      f"en gec {hold_ms // 3_600_000} saat sonra satilir (buyuk mum + kirmizi mum kurali).")

    def _manage(self, state, amounts, open_ids, now_ms):
        for symbol, pos in list(state["positions"].items()):
            if amounts.get(symbol, Decimal("0")) <= 0:        # the stop fired
                self._record(state, symbol, Decimal(pos["stop"]), "stop", now_ms)
            elif now_ms >= pos["until"]:
                self._exit(state, symbol, "sure doldu")

    def _exit(self, state, symbol, reason):
        from .binance_trade import OrderStateUnknown
        pos = state["positions"][symbol]
        try:
            self.executor.cancel(symbol, pos["stop_id"])
        except Exception as exc:
            print(f"Spike rule cancel failed ({symbol}): {exc}", flush=True)
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
        state["closed"].append({"symbol": symbol, "entry": float(entry), "exit": float(price), "pnl": pnl,
                                "reason": reason, "opened_at": pos["opened_at"], "closed_at": at_ms})
        total = sum(r["pnl"] for r in state["closed"])
        wins = sum(1 for r in state["closed"] if r["pnl"] > 0)
        self._say(f"KAPANDI ({reason}): {symbol.removesuffix('USDT')} {pos['entry']} -> {price} | "
                  f"{float(price / entry - 1) * 100:+.1f}% (yaklasik {pnl:+.2f} USDT) | mum toplam: "
                  f"{len(state['closed'])} islem, {wins} kazanan, {total:+.2f} USDT")
