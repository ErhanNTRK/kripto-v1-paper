"""Dip-catcher ("igne yakalayici"), PAPER mode (user's decision, 29-30 Sep 2026).

Every UTC day it "rests" limit buys on the day's top-N coins of the ranked
daily list at yesterday's daily close x (1 - depth). A resting order fills
on the first CLOSED 5-minute candle whose low reaches it (at the limit, or
at that candle's open when it opened below). A filled position leaves at a
take-profit (never on the fill candle: its high most likely came before
the drop), at a stop (checked first, the fill candle included), or at the
close of the first candle after hold_hours. Costs per side as in the
research. Orders left unfilled expire at the next UTC day start.

Research (research/pit/crash_portfolio2.py, 29 Sep 2026): top 10, -15%,
+8% or 1 day, -25% stop, 10% of the pot per order was positive in both
2021-23 (12-month median +15%, worst +5%) and 2023-26 (+16%, worst -8%),
with drawdowns of 17-33%.

Paper only: no order ever reaches Binance from here. It only tells the
user on Telegram what it would have done, so the live behaviour can be
checked before real orders are wired in. A coin the bot already holds is
skipped (one-way account)."""
import json
import time
from pathlib import Path

DAY_MS = 86_400_000
FIVE_MIN_MS = 300_000
COST = 0.0007  # fee + slippage per side, as in the research


def _read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value), encoding="utf-8")
    tmp.replace(path)


def _public_klines(symbol, interval, start_ms=None, limit=1000):
    from .data import get
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if start_ms is not None:
        params["startTime"] = int(start_ms)
    return get("klines", params)


def _fmt(price):
    return f"{price:.6g}"


class DipCatcher:
    def __init__(self, config, state_path, manifest_path, send=None, klines=None, clock=time.time,
                 equity_fn=None, held_fn=None):
        self.config = dict(config or {})
        self.state_path, self.manifest_path = state_path, manifest_path
        if send is None:
            from .telegram import send_message as send
        self.send, self.klines, self.clock = send, klines or _public_klines, clock
        self.equity_fn, self.held_fn = equity_fn, held_fn

    @property
    def enabled(self):
        return self.config.get("mode") == "paper"

    def _say(self, text):
        try:
            self.send("[IGNE-KAGIT] " + text)
        except Exception as exc:
            print(f"Dip-catcher Telegram failed: {exc}", flush=True)

    def _closed(self, symbol, since_ms, now_ms):
        """Closed 5-minute candles from since_ms on, as (open_ms, o, h, l, c, close_ms)."""
        rows = self.klines(symbol, "5m", since_ms)
        return [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), int(r[6]))
                for r in rows if int(r[6]) < now_ms and int(r[0]) >= since_ms]

    def tick(self):
        if not self.enabled:
            return None
        now_ms = int(self.clock() * 1000)
        state = _read(self.state_path, {}) or {}
        state.setdefault("orders", {}); state.setdefault("positions", {}); state.setdefault("closed", [])
        day = now_ms // DAY_MS * DAY_MS
        if state.get("day") != day:
            if state.get("day") is not None:
                self._check_orders(state, now_ms)   # yesterday's orders, up to their own day end
            self._new_day(state, day, now_ms)
        self._check_orders(state, now_ms)
        self._check_positions(state, now_ms)
        _write(self.state_path, state)
        return state

    def _new_day(self, state, day, now_ms):
        state["day"], state["orders"] = day, {}
        top_n = int(self.config.get("top_n", 10))
        depth = float(self.config.get("depth", 0.15))
        ranked = [s for s in (_read(self.manifest_path, {}) or {}).get("symbols", []) if s != "BTCUSDT"][:top_n]
        held = set(state["positions"])
        if self.held_fn is not None:
            try:
                held |= set(self.held_fn())
            except Exception as exc:
                print(f"Dip-catcher held symbols unreadable: {exc}", flush=True)
        equity = None
        if self.equity_fn is not None:
            try:
                equity = float(self.equity_fn())
            except Exception as exc:
                print(f"Dip-catcher equity unreadable: {exc}", flush=True)
        equity = equity or float(self.config.get("paper_equity_usdt", 100))
        size = round(equity * float(self.config.get("size_fraction", 0.10)), 2)
        placed = []
        for symbol in ranked:
            if symbol in held:
                continue
            try:
                daily = self.klines(symbol, "1d", None, 3)
            except Exception as exc:
                print(f"Dip-catcher daily close unreadable ({symbol}): {exc}", flush=True)
                continue
            prev = [r for r in daily if int(r[0]) == day - DAY_MS and int(r[6]) < now_ms]
            if not prev:
                continue
            limit = float(prev[0][4]) * (1 - depth)
            state["orders"][symbol] = {"limit": limit, "size_usdt": size, "checked_until": day}
            placed.append(f"{symbol.removesuffix('USDT')} {_fmt(limit)}")
        if placed:
            self._say(f"Bugunun bekleyen alim emirleri (dunku kapanisin %{depth * 100:.0f} alti, her biri "
                      f"~{size:.2f} USDT): " + ", ".join(placed) + ". Gercek emir yok, sadece takip.")

    def _check_orders(self, state, now_ms):
        stop_f = float(self.config.get("stop", 0.25))
        tp_f = float(self.config.get("take_profit", 0.08))
        hold_ms = int(float(self.config.get("hold_hours", 24)) * 3_600_000)
        for symbol, order in list(state["orders"].items()):
            if now_ms - order["checked_until"] < FIVE_MIN_MS:
                continue
            try:
                candles = self._closed(symbol, order["checked_until"], now_ms)
            except Exception as exc:
                print(f"Dip-catcher candles unreadable ({symbol}): {exc}", flush=True)
                continue
            for open_ms, o, h, l, c, close_ms in candles:
                if open_ms >= state["day"] + DAY_MS:     # the order expired at its day end
                    break
                order["checked_until"] = close_ms + 1
                if l > order["limit"]:
                    continue
                entry = min(order["limit"], o) * (1 + COST)
                position = {"entry": entry, "qty": order["size_usdt"] / entry, "tp": entry * (1 + tp_f),
                            "stop": entry * (1 - stop_f), "filled_at": open_ms, "until": open_ms + hold_ms,
                            "checked_until": close_ms + 1, "last": c}
                del state["orders"][symbol]
                state["positions"][symbol] = position
                self._say(f"DOLDU: {symbol.removesuffix('USDT')} {_fmt(entry)} | hedef {_fmt(position['tp'])} "
                          f"(+%{tp_f * 100:.0f}), stop {_fmt(position['stop'])} (-%{stop_f * 100:.0f}), "
                          f"en gec {hold_ms // 3_600_000} saat sonra kapanir.")
                if l <= position["stop"]:          # the same candle went on to the stop: conservative
                    self._close(state, symbol, position["stop"], close_ms, "stop (ayni mum)")
                break

    def _check_positions(self, state, now_ms):
        for symbol, p in list(state["positions"].items()):
            if now_ms - p["checked_until"] < FIVE_MIN_MS:
                continue
            try:
                candles = self._closed(symbol, p["checked_until"], now_ms)
            except Exception as exc:
                print(f"Dip-catcher candles unreadable ({symbol}): {exc}", flush=True)
                continue
            for open_ms, o, h, l, c, close_ms in candles:
                p["checked_until"], p["last"] = close_ms + 1, c
                if l <= p["stop"]:
                    self._close(state, symbol, min(o, p["stop"]), close_ms, "stop"); break
                if h >= p["tp"]:
                    self._close(state, symbol, max(o, p["tp"]), close_ms, "hedef"); break
                if close_ms + 1 >= p["until"]:
                    self._close(state, symbol, c, close_ms, "sure doldu"); break

    def _close(self, state, symbol, price, at_ms, reason):
        p = state["positions"].pop(symbol)
        out = price * (1 - COST)
        pnl = p["qty"] * (out - p["entry"])
        pct = out / p["entry"] - 1
        state["closed"].append({"symbol": symbol, "entry": p["entry"], "exit": out, "pnl": pnl, "pct": pct,
                                "reason": reason, "filled_at": p["filled_at"], "closed_at": at_ms})
        wins = sum(1 for r in state["closed"] if r["pnl"] > 0)
        total = sum(r["pnl"] for r in state["closed"])
        self._say(f"KAPANDI ({reason}): {symbol.removesuffix('USDT')} {_fmt(p['entry'])} -> {_fmt(out)} | "
                  f"{pct * 100:+.1f}% ({pnl:+.2f} USDT) | kagit toplam: {len(state['closed'])} islem, "
                  f"{wins} kazanan, {total:+.2f} USDT")
