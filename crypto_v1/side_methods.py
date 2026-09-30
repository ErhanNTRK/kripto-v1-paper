"""Two more entry methods in the 2H sleeve (user's decision, 30 Sep 2026: "every profitable method goes live today").

Daily cup rim breakout ("canak", research/pit/cup_breakout.py, cup_portfolio.py): the day that just closed is the
first close above the highest close of the 120 days before it (the rim); after the rim the low fell 20-50%, the
trough came at least 7 days after the rim and at least 10 days before the breakout, the rim is 20-120 days old;
BTC's daily close is above its 200- and 50-day averages. Bought at market, stop 2 daily ATR under the price,
sold after 20 days. Own pot test, 12 months per 100 USD: typical 121 (2021-23, never a loss) and 151 (2023-26).

4H short cup ("kisa canak", research/pit/short_cup.py): the 4H bar that just closed is the first close above the
highest close of the 60 bars before it, 8-60 bars after that rim, with a trough 8-25% under the rim at least 3
bars after it and at least 3 bars before the breakout. Bought at market, stop 2 ATR under, take-profit +3%,
sold after 24 hours. Thin edge (PF 1.05-1.12 in both eras) but frequent, about 12 a week.

SideScanner finds the candidates on each new daily / 4H close from Binance's public klines; SignalTrader takes
them (one position per coin, never a coin the account holds or where another of our orders rests), sized at
risk_fraction of the 2H sleeve against the stop, 3x isolated, with a reduce-only stop, an optional take-profit
and a market sale at the time limit. Deterministic client ids (prefix + symbol code + bar time)."""
import time
from decimal import Decimal

from .dip_catcher import _read, _write

DAY_MS = 86_400_000
FOUR_HOUR_MS = 4 * 3_600_000


def _rows(klines, now_ms):
    return [{"t": int(r[0]), "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4])}
            for r in klines if int(r[6]) < now_ms]


def _sma_atr(rows, n=14):
    """Simple mean true range of the last n bars (the daily research's ATR)."""
    trs = [max(rows[j]["h"] - rows[j]["l"], abs(rows[j]["h"] - rows[j - 1]["c"]), abs(rows[j]["l"] - rows[j - 1]["c"]))
           for j in range(max(1, len(rows) - n), len(rows))]
    return sum(trs) / len(trs) if trs else None


def _ema_atr(rows, n=14):
    """Wilder-style running true range (the 4H research's ATR)."""
    atr = None
    for i, r in enumerate(rows):
        tr = r["h"] - r["l"] if i == 0 else max(r["h"] - r["l"], abs(r["h"] - rows[i - 1]["c"]), abs(r["l"] - rows[i - 1]["c"]))
        atr = tr if atr is None else atr + (tr - atr) / n
    return atr


def daily_cup(rows, btc_rows):
    """rows / btc_rows: closed daily bars, oldest first; the last one is the day that just closed."""
    if len(rows) < 122 or len(btc_rows) < 200:
        return None
    bc = [r["c"] for r in btc_rows]
    if not (bc[-1] > sum(bc[-200:]) / 200 and bc[-1] > sum(bc[-50:]) / 50):
        return None
    closes = [r["c"] for r in rows]
    i = len(rows) - 1
    window = closes[i - 120:i]
    P = max(window)
    if not (closes[i] > P and closes[i - 1] <= P):
        return None
    p = i - 120 + window.index(P)
    if not 20 <= i - p <= 180:
        return None
    tq = min(range(p + 1, i), key=lambda k: rows[k]["l"])
    depth = 1 - rows[tq]["l"] / P
    if not (0.20 <= depth <= 0.50 and tq - p >= 7 and i - tq >= 10):
        return None
    atr = _sma_atr(rows)
    return {"close": closes[i], "atr": atr, "time": rows[i]["t"]} if atr else None


def short_cup(rows):
    """rows: closed 4H bars, oldest first; the last one is the bar that just closed."""
    if len(rows) < 80:
        return None
    closes = [r["c"] for r in rows]
    i = len(rows) - 1
    window = closes[i - 60:i]
    P = max(window)
    if not (closes[i] > P and closes[i - 1] <= P):
        return None
    p = i - 60 + window.index(P)
    if not 8 <= i - p <= 60:
        return None
    tq = min(range(p + 1, i), key=lambda k: rows[k]["l"])
    depth = 1 - rows[tq]["l"] / P
    if not (0.08 <= depth <= 0.25 and tq - p >= 3 and i - tq >= 3):
        return None
    atr = _ema_atr(rows)
    return {"close": closes[i], "atr": atr, "time": rows[i]["t"]} if atr else None


def _public_klines(symbol, interval, limit):
    from .data import get
    return get("klines", {"symbol": symbol, "interval": interval, "limit": limit})


class SideScanner:
    """Writes each method's candidates once per new closed bar (daily at 00:00 UTC, 4H every 4 hours)."""

    def __init__(self, manifest_path, daily_path, short_path, klines=None, clock=time.time):
        self.manifest_path, self.daily_path, self.short_path = manifest_path, daily_path, short_path
        self.klines, self.clock = klines or _public_klines, clock

    def _symbols(self):
        return [s for s in (_read(self.manifest_path, {}) or {}).get("symbols", []) if s != "BTCUSDT"]

    def tick(self):
        now_ms = int(self.clock() * 1000)
        day = now_ms // DAY_MS * DAY_MS
        if (_read(self.daily_path, {}) or {}).get("window") != day:
            found = []
            btc = _rows(self.klines("BTCUSDT", "1d", 260), now_ms)
            for symbol in self._symbols():
                try:
                    c = daily_cup(_rows(self.klines(symbol, "1d", 200), now_ms), btc)
                except Exception as exc:
                    print(f"Daily cup scan failed ({symbol}): {exc}", flush=True)
                    continue
                if c:
                    found.append(dict(c, symbol=symbol))
            _write(self.daily_path, {"window": day, "bar_ms": DAY_MS, "candidates": found})
        window = now_ms // FOUR_HOUR_MS * FOUR_HOUR_MS
        if (_read(self.short_path, {}) or {}).get("window") != window:
            found = []
            for symbol in self._symbols():
                try:
                    c = short_cup(_rows(self.klines(symbol, "4h", 120), now_ms))
                except Exception as exc:
                    print(f"Short cup scan failed ({symbol}): {exc}", flush=True)
                    continue
                if c:
                    found.append(dict(c, symbol=symbol))
            _write(self.short_path, {"window": window, "bar_ms": FOUR_HOUR_MS, "candidates": found})


class SignalTrader:
    LEVERAGE = 3

    def __init__(self, config, label, prefixes, state_path, signal_path, executor, market, send=None,
                 clock=time.time, equity_fn=None):
        """prefixes: {"open", "stop", "exit"} and optionally "tp" (4-char+ client id prefixes)."""
        self.config, self.label, self.prefixes = dict(config or {}), label, prefixes
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
            self.send(f"[{self.label}] " + text)
        except Exception as exc:
            print(f"{self.label} Telegram failed: {exc}", flush=True)

    @staticmethod
    def _id(prefix, symbol, bar_time):
        from .live_short_market import symbol_code
        return f"{prefix}{symbol_code(symbol)}{int(bar_time)}"

    def tick(self):
        if not self.enabled:
            return None
        now_ms = int(self.clock() * 1000)
        state = _read(self.state_path, {}) or {}
        state.setdefault("positions", {}); state.setdefault("closed", []); state.setdefault("seen", [])
        account = self.executor.account()
        amounts = {p["symbol"]: Decimal(str(p.get("positionAmt") or "0")) for p in account.get("positions", [])}
        open_orders = self.executor.open_orders()
        open_ids = {str(o.get("clientOrderId", "")) for o in open_orders}
        busy = {o.get("symbol") for o in open_orders if str(o.get("clientOrderId", "")).startswith("kv1f")}
        self._manage(state, amounts, open_ids, now_ms)
        self._enter(state, amounts, busy, now_ms)
        _write(self.state_path, state)
        return state

    def _enter(self, state, amounts, busy, now_ms):
        from .binance_trade import OrderStateUnknown
        from .live_execution import MIN_NOTIONAL_HEADROOM, _down, _up
        signals = _read(self.signal_path, {}) or {}
        bar_ms = int(signals.get("bar_ms") or 0)
        max_age = int(self.config.get("max_age_minutes", 30)) * 60_000
        for c in signals.get("candidates", []):
            key = f"{c['symbol']}:{c['time']}"
            if key in state["seen"]:
                continue
            state["seen"] = (state["seen"] + [key])[-300:]
            symbol, bar_close = c["symbol"], int(c["time"]) + bar_ms
            if now_ms - bar_close > max_age:
                print(f"{self.label} signal too old, skipped: {symbol}", flush=True)
                continue
            if symbol in state["positions"] or amounts.get(symbol, Decimal("0")) != 0 or symbol in busy:
                print(f"{self.label} signal skipped, coin busy: {symbol}", flush=True)
                continue
            try:
                equity = Decimal(str(self.equity_fn()))
                rules = self.market.rules(symbol)
                price = Decimal(str(self.market.price(symbol)))
                stop = _down(price - Decimal(str(self.config.get("stop_atr", 2.0))) * Decimal(str(c["atr"])),
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
                print(f"{self.label} could not prepare {symbol}: {exc}", flush=True)
                continue
            ids = {k: self._id(p, symbol, c["time"]) for k, p in self.prefixes.items()}
            try:
                result = self.executor.market_open_long(symbol, format(qty, "f"), ids["open"])
            except OrderStateUnknown:
                result = self.executor.query(symbol, ids["open"])
            except Exception as exc:
                print(f"{self.label} buy rejected ({symbol}): {exc}", flush=True)
                continue
            filled = Decimal(str(result.get("executedQty") or "0"))
            if filled <= 0:
                continue
            entry = Decimal(str(result.get("avgPrice") or "0")) or price
            hold_ms = int(float(self.config.get("hold_hours", 24)) * 3_600_000)
            pos = {"qty": format(filled, "f"), "entry": format(entry, "f"), "stop": format(stop, "f"),
                   "stop_id": ids["stop"], "tp_id": ids.get("tp"), "exit_id": ids["exit"],
                   "opened_at": now_ms, "until": now_ms + hold_ms, "tp": None}
            state["positions"][symbol] = pos
            _write(self.state_path, state)
            try:
                self.executor.protective_stop_for_long(symbol, pos["qty"], pos["stop"], pos["stop_id"])
            except Exception as exc:
                print(f"{self.label} stop failed ({symbol}): {exc}; closing at market", flush=True)
                self._exit(state, symbol, "acil kapatma (stop konamadi)")
                continue
            tp_frac = self.config.get("take_profit")
            if tp_frac and pos["tp_id"]:
                pos["tp"] = format(_up(entry * (1 + Decimal(str(tp_frac))), rules["tick_size"]), "f")
                try:
                    self.executor.take_profit_for_long(symbol, pos["qty"], pos["tp"], pos["tp_id"])
                except Exception as exc:
                    print(f"{self.label} take-profit failed ({symbol}): {exc}", flush=True)
            hedef = f", hedef {pos['tp']}" if pos["tp"] else ""
            self._say(f"ALDIM: {symbol.removesuffix('USDT')} {pos['qty']} @ {pos['entry']} | stop {pos['stop']}{hedef} | "
                      f"en gec {hold_ms // 3_600_000} saat sonra satilir.")

    def _manage(self, state, amounts, open_ids, now_ms):
        for symbol, pos in list(state["positions"].items()):
            if amounts.get(symbol, Decimal("0")) <= 0:
                tp_gone = pos.get("tp_id") and pos["tp_id"] not in open_ids
                stop_open = pos["stop_id"] in open_ids
                if tp_gone and stop_open:
                    reason, price = "hedef", pos["tp"]
                else:
                    reason, price = "stop", pos["stop"]
                for cid in (pos.get("tp_id"), pos["stop_id"]):
                    if cid and cid in open_ids:
                        self._quiet_cancel(symbol, cid)
                self._record(state, symbol, Decimal(price), reason, now_ms)
            elif now_ms >= pos["until"]:
                self._exit(state, symbol, "sure doldu")

    def _quiet_cancel(self, symbol, client_id):
        try:
            self.executor.cancel(symbol, client_id)
        except Exception as exc:
            print(f"{self.label} cancel failed ({symbol} {client_id}): {exc}", flush=True)

    def _exit(self, state, symbol, reason):
        from .binance_trade import OrderStateUnknown
        pos = state["positions"][symbol]
        for cid in (pos.get("tp_id"), pos["stop_id"]):
            if cid:
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
        state["closed"].append({"symbol": symbol, "entry": float(entry), "exit": float(price), "pnl": pnl,
                                "reason": reason, "opened_at": pos["opened_at"], "closed_at": at_ms})
        total = sum(r["pnl"] for r in state["closed"])
        wins = sum(1 for r in state["closed"] if r["pnl"] > 0)
        self._say(f"KAPANDI ({reason}): {symbol.removesuffix('USDT')} {pos['entry']} -> {price} | "
                  f"{float(price / entry - 1) * 100:+.1f}% (yaklasik {pnl:+.2f} USDT) | toplam: "
                  f"{len(state['closed'])} islem, {wins} kazanan, {total:+.2f} USDT")
