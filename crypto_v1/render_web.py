"""Render Frankfurt health probe and authenticated Telegram command webhook."""
import collections, hmac, json, os, re, subprocess, sys, threading, time
from datetime import datetime, timezone
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from .binance_account import verify_from_environment
from .binance_futures import FuturesExecutor
from .binance_trade import OrderRejected, SpotExecutor
from . import ledger
from .data import universe
from .github_worker import local_tick
from .live_controller import _known_or_place
from .live_execution import _down, _up, execution_enabled
from .live_limits import may_open, trade_risk_usdt
from .live_market import BinanceMarket
from .live_monitor import execute_exit, exit_decision
from .live_short_controller import approve_long_leveraged, approve_short
from .live_short_market import BinanceFuturesMarket, symbol_code
from .live_short_monitor import (cancel_quietly, execute_long_futures_exit, execute_short_exit, open_amount,
                                 short_exit_decision)
from .live_signal import (RUNTIME_STATE, RUNTIME_STATE_SHORT, fetch_runtime_state,
                          fetch_runtime_state_short, isolate_candidate,
                          isolate_short_candidate, pending_candidates,
                          pending_short_candidates)
from .research_v2 import FOUR_HOUR, TWO_HOUR
from .research_v5 import ShortWindowLongModel, symmetric_features
from .telegram import send_message
from .telegram_poll import TelegramApprovals

def _running_commit():
    """The commit this process actually loaded. The PC launcher sets
    RENDER_GIT_COMMIT once per window, but its restart loop keeps that value
    while the checkout underneath moves on -- /health showed 0da470a on 24
    Sep 2026 while 6d4f517 was running. The checkout itself is asked first."""
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()[:12]
    except Exception:
        pass
    return os.environ.get("RENDER_GIT_COMMIT", "")[:12]


# "commit" proves which build is running (see _running_commit) -- a /health
# read answers "is the fix live?" without dashboard access.
STATUS = {"ready": False, "binance_connected": False, "orders_enabled": False, "telegram_ready": False,
          "commit": os.environ.get("RENDER_GIT_COMMIT", "")[:12]}
APP = None
APPS = []

# Process-wide circuit breaker: -1003 is Binance's IP-level "too many
# requests" rejection. Live-tripped 18 Sep 2026 by a burst of pilot_status()
# calls (see LiveApp.ENTRIES_PER_TICK's docstring); the resulting ban was
# observed to outlast a single fixed 5-minute cooldown -- probing again
# right as each cooldown expired kept re-hitting -1003 for 25+ minutes
# straight. Cooldown now doubles on each consecutive hit (5, 10, 20, 40...
# minutes, capped at 1h) so a longer real ban is probed less and less
# often instead of being hammered every 5 minutes regardless of its
# actual length.
#
# The escalation must NOT reset itself by elapsed time: by construction, a
# tick only ever runs again right as the PREVIOUS cooldown expires, so the
# gap between consecutive hits during a real, ongoing ban is always
# approximately equal to that previous cooldown -- a time-based "gap this
# big means it's a new incident" check was tried first and immediately
# defeated its own purpose (live-observed 18 Sep 2026: the escalation got
# stuck oscillating at 2x because the 10-minute cooldown was itself just
# over the reset threshold). The escalation resets only on a genuine
# success signal instead: _note_rate_limit_cleared(), called once per tick
# that completes without a fresh hit. Shared across BOTH apps (4H and 2H)
# since the ban is per source IP/account, not per app.
_RATE_LIMIT_BASE_COOLDOWN_S = 300
_RATE_LIMIT_MAX_COOLDOWN_S = 3600
_rate_limited_until = 0.0
_consecutive_rate_limit_hits = 0
_rate_limit_last_message = ""
_BANNED_UNTIL = re.compile(r"banned until (\d{10,})")


def _rate_limited():
    return time.time() < _rate_limited_until


def _note_if_rate_limited(exc):
    """Besides the doubling cooldown, honor Binance's own ban clock when it
    gives one. HTTP 418 (-1003) is an IP ban, not a per-minute limit: its
    msg reads "...IP banned until <epoch ms>...", the duration escalates for
    repeat offenders (2 minutes up to 3 days), and EVERY request sent
    before that time counts as a fresh violation that extends it. Live-
    observed 18 Sep 2026: three consecutive hits on the already-lightened
    code, one right after each cooldown expired -- consistent with probing
    inside a ban rather than with our own request weight. Pausing until the
    later of the two clocks stops the loop from feeding the ban."""
    global _rate_limited_until, _consecutive_rate_limit_hits, _rate_limit_last_message
    if isinstance(exc, OrderRejected) and exc.code == -1003:
        _consecutive_rate_limit_hits += 1
        cooldown = min(_RATE_LIMIT_MAX_COOLDOWN_S,
                       _RATE_LIMIT_BASE_COOLDOWN_S * (2 ** (_consecutive_rate_limit_hits - 1)))
        until = time.time() + cooldown
        _rate_limit_last_message = f"HTTP {exc.http_status}: {exc.message}" if exc.http_status else exc.message
        match = _BANNED_UNTIL.search(exc.message or "")
        if match:
            banned_until = int(match.group(1)) / 1000  # Binance reports epoch milliseconds
            until = max(until, banned_until + 5)
        _rate_limited_until = until
        print(f"Binance rate limit hit ({_consecutive_rate_limit_hits}x in a row); "
             f"pausing all Binance calls for {int(until - time.time())}s; {_rate_limit_last_message}",
             flush=True)


def _note_rate_limit_cleared():
    global _consecutive_rate_limit_hits
    _consecutive_rate_limit_hits = 0


def _fill_pnl(entry, order, side):
    """Realized PNL from an exit fill's own reported quote amount, vs. the
    position's recorded entry price -- None if the fill has no usable
    quote/qty (e.g. an "already_stopped" result with no order field)."""
    if not order:
        return None
    qty = Decimal(str(order.get("executedQty", "0")))
    # Spot reports the fill's quote total as cummulativeQuoteQty; Futures
    # (/fapi) calls the same thing cumQuote -- reading only the Spot name
    # meant every futures close reported no PNL at all.
    quote = Decimal(str(order.get("cummulativeQuoteQty") or order.get("cumQuote") or "0"))
    if qty <= 0 or quote <= 0:
        return None
    entry_value = qty * Decimal(str(entry))
    return quote - entry_value if side == "long" else entry_value - quote


def _pnl_suffix(pnl):
    return f" | PNL: {pnl:+.2f} USDT" if pnl is not None else ""

def telegram_command(payload, expected_chat):
    message = payload.get("message", {})
    chat = message.get("chat", {})
    if chat.get("type") != "private" or str(chat.get("id", "")) != str(expected_chat): return None
    text = str(message.get("text", "")).strip()
    if not text: return None
    return {"update_id": int(payload["update_id"]), "command": text}

def _fresh_stop_id(stop_id):
    """A new client id for a replacement stop: the base id plus the time.
    Any earlier replacement suffix is dropped first -- appending again on the
    second move made a 45-character id, which Binance rejects (-4015, limit
    36) AFTER the old stop was already cancelled (NILUSDT, 24 Sep 2026)."""
    return f"{str(stop_id).split('t', 1)[0]}t{int(time.time())}"


def _decision_view(position):
    """The position as the exit rules must see it. They measure risk as
    entry-to-stop and read a stop at or past entry as "no risk left" ->
    emergency close. That held while the resting stop never moved; once the
    ratchet (or a re-placed stop) puts it in profit, every such trade would be
    cut at once instead of trailing as tested -- NILUSDT was closed that way
    on 24 Sep 2026. A stop only gets there after the trade is 1R ahead, so it
    is judged as an armed trade: a minimal risk keeps the signal and trailing
    checks exactly as they are."""
    stop = position.get("stop_price")
    if stop is None:
        return position
    entry, stop = Decimal(str(position["entry"])), Decimal(str(stop))
    tiny = entry * Decimal("1e-9")
    if position["side"] == "short" and stop <= entry:
        return {**position, "stop_price": entry + tiny}
    if position["side"] != "short" and stop >= entry:
        return {**position, "stop_price": entry - tiny}
    return position


def _utc_day(now_s):
    return datetime.fromtimestamp(now_s, timezone.utc).date().isoformat()


def _read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value), encoding="utf-8")
    tmp.replace(path)


class LiveApp:
    # See auto_enter's docstring: bounds Binance API weight per automatic
    # tick so a burst of simultaneous candidates can never itself trip a
    # rate limit and starve the same tick's exit check. Raised from 1 to 3
    # on 24 Sep 2026: at one per 2-minute loop the fifth candidate of a bar
    # went in ~10 minutes late, and the research's best trades come from
    # exactly those many-candidate bars. pilot_status now reads the daily
    # list instead of re-ranking the market, and contract rules are cached,
    # so three stay well inside Binance's weight budget.
    ENTRIES_PER_TICK = 3
    # One lock for every app: the 4H and 2H systems share one one-way
    # account, and a /scan request ticking one system while the loop ticked
    # the other could let both buy the same coin.
    _SHARED_TICK_LOCK = threading.Lock()

    def __init__(self, config, strategy_config, environment, short_config=None, short_strategy_config=None,
                 tag="", state_url=RUNTIME_STATE, state_short_url=RUNTIME_STATE_SHORT, interval=FOUR_HOUR,
                 manifest_path=None, guard_path=None, universe_fn=None):
        self.config, self.environment = config, environment
        # scan()'s exit check must fetch/analyze candles on THIS system's
        # own interval (2H for tag "2", 4H for tag "4"). Bug found live
        # 21 Sep 2026: it was hardcoded to FOUR_HOUR for every app, so a
        # position the 2H system opened -- often mid-way through the
        # CURRENT, still-open 4H bar -- had its buy_time floored to that
        # in-progress 4H bar's start, which has no closed candle yet:
        # "Spot exit scan failed: max() iterable argument is empty",
        # meaning the just-opened ADA/AAVE positions' trailing/trend exit
        # was never actually being checked (the exchange-native protective
        # stop was still resting regardless -- that part never depended on
        # this loop).
        self.interval = interval
        # `tag` segregates capital/positions between parallel systems
        # sharing one real Binance account (18 Sep 2026: 4H tag="4", 2H
        # tag="2") -- see live_market.summarize_pilot / _belongs_to_tag.
        # `state_url`/`state_short_url` let each tagged system read its own
        # independently-published candidate feed (see github_worker.
        # write_2h_signal_state) instead of always reading the 4H files.
        self.tag = tag
        self.state_url, self.state_short_url = state_url, state_short_url
        self._tick_lock = LiveApp._SHARED_TICK_LOCK
        # The daily-ranked coin list (runtime/manifest.json). With it, only
        # the top auto_entry_top_n coins are bought automatically and the
        # rest wait for the user's Telegram "AL <COIN>" (user's decision, 24
        # Sep 2026). None (tests, Render) means no gate.
        self.manifest_path = manifest_path
        # Where the daily equity stop keeps its day-start snapshot, so a
        # restart mid-day does not forget it. None keeps it in memory only.
        self.guard_path = guard_path
        self._guard = None
        # Approval requests for coins outside the automatic list:
        # {symbol: {"created_at", "expires_ms", "side", "approved"}}. Written
        # by the loop, approved from the Telegram polling thread.
        self._approvals = {}
        self._approvals_lock = threading.Lock()
        # Most recent tick's full result (rejection reasons included) -- what
        # /status serves so a rejected entry can be diagnosed without Render
        # log access and without spending any Binance weight.
        self.last_tick = None
        # last_tick alone only ever showed the LATEST tick, and a candidate
        # lives ~30 minutes: on 22 Sep 2026 every attempt had been rejected
        # ("rounded plan exceeds risk budget") and overwritten by empty
        # ticks long before anyone looked. /status now keeps the recent
        # attempts themselves.
        self.recent_entries = collections.deque(maxlen=30)
        self._notified_skips = set()
        self._pilot_alert_day = None
        self._scan_failures = 0
        self.executor = SpotExecutor(config, environment)
        self.market = BinanceMarket(config, strategy_config, environment, self.executor, tag=tag)
        self.short_config = short_config
        # `is not None`, not truthiness: an empty-but-present short_config
        # dict must still enable the short subsystem, not silently disable
        # it the way a falsy `if short_config` check would.
        self.futures_executor = FuturesExecutor(short_config, environment) if short_config is not None else None
        self.futures_market = (BinanceFuturesMarket(short_config, short_strategy_config, environment,
                                                     self.futures_executor, tag=tag, universe_fn=universe_fn)
                               if short_config is not None else None)

    def _approve_long(self, update_id, saved, tag=""):
        # Leveraged Futures long (21 Sep 2026), replacing unleveraged Spot
        # for NEW entries per the user's explicit decision -- 4H's 3x/5x
        # strength tiering matches the fully walk-forward-validated (5/5
        # GO) 4H signal; 2H's fixed 2x reflects its NO_GO-overall (3/5)
        # edge. Uses self.short_config/futures_market/futures_executor
        # (the SAME Futures wallet the short side already trades on, see
        # live_short_market.summarize_short_pilot's combined accounting)
        # instead of self.config/market/executor (Spot) -- an
        # already-open Spot long from before this change still exits
        # through the unmodified Spot path in scan() below, untouched.
        result = approve_long_leveraged(update_id, "AL", int(time.time() * 1000),
                                        saved, self.short_config, self.environment,
                                        self.futures_market, self.futures_executor)
        label = f"[{tag}] " if tag else ""
        if result["status"] == "preview":
            plan = result["plan"]
            send_message(f"{label}AL onayi dogrulandi: {plan['symbol']} | {plan['leverage']}x kaldirac | "
                         f"Planlanan risk {plan['planned_loss_usdt']} USDT. Gercek emir kilidi kapali.")
        elif result["status"] == "opened_and_protected":
            plan = result["plan"]
            send_message(f"{label}ALDIM: {plan['symbol']} | {plan['leverage']}x kaldirac | risk {plan['planned_loss_usdt']} USDT")
        elif result["status"] == "opened_then_emergency_closed":
            send_message(f"{label}ACIL GUVENLIK KAPATMASI: {result['plan']['symbol']} "
                         f"({result.get('reason')}) long geri kapatildi.")
        # "no pending signal" / limit-hit rejections happen on almost every
        # scan cycle now that entry is automatic -- silent, not spammed.
        return result

    def _approve_short(self, update_id, saved, tag=""):
        result = approve_short(update_id, "AL", int(time.time() * 1000),
                               saved, self.short_config, self.environment,
                               self.futures_market, self.futures_executor)
        label = f"[{tag}] " if tag else ""
        if result["status"] == "preview":
            plan = result["plan"]
            send_message(f"{label}SHORT onayi dogrulandi: {plan['symbol']} | {plan['leverage']}x kaldirac | "
                         f"Planlanan risk {plan['planned_loss_usdt']} USDT. Gercek emir kilidi kapali.")
        elif result["status"] == "opened_and_protected":
            plan = result["plan"]
            send_message(f"{label}ALDIM (SHORT): {plan['symbol']} | {plan['leverage']}x kaldirac")
        elif result["status"] == "opened_then_emergency_closed":
            send_message(f"{label}ACIL GUVENLIK KAPATMASI: {result['plan']['symbol']} "
                         f"({result.get('reason')}) short geri kapatildi.")
        return result

    def auto_enter(self):
        """Executes the current pending signal(s) automatically -- no
        Telegram "AL" reply needed -- per the user's 18 Sep 2026
        instruction (he will not be at a computer to reply). Reuses the
        exact same confirmed/approve machinery the old AL-triggered path
        used (that path still works too, harmlessly redundant), just
        triggered by the periodic scan loop instead of an incoming
        Telegram message. Keyed by each candidate's own signal timestamp
        (not a Telegram update_id), so a candidate seen again on a later
        scan cycle -- still within its freshness window -- reuses the
        SAME client order id and _known_or_place recognizes it as
        already-placed instead of opening it twice.

        Capped at ENTRIES_PER_TICK per side (see class constant): each
        approve_buy/approve_short call does its own fresh market.
        pilot_status() (a full account+all-orders-per-universe-symbol
        Binance scan) to re-check limits, so taking every pending
        candidate in one tick multiplies that heavy call by the candidate
        count. Live-tested 18 Sep 2026: 5-6 simultaneous candidates
        (normal with min_breaks=1/very_loose/top_n=50) tripped Binance's
        -1003 rate limit mid-batch and crashed the SAME tick's exit scan
        right after, meaning open positions momentarily went unchecked --
        a real safety gap, not just a missed entry. Untaken candidates are
        not lost: pending_buys/pending_shorts persist until filled or
        their signal_confirmation_expiry_minutes window lapses, so the
        rest get taken over the next few 5-minute ticks instead of all at
        once."""
        now_ms = int(time.time() * 1000)
        if self.halted_today():
            return {"status": "auto_entry", "results": [], "halted": "daily_equity_stop"}
        gate = self._auto_symbols()
        self._forget_expired_approvals(now_ms)
        saved_long = fetch_runtime_state(url=self.state_url)
        saved_short = fetch_runtime_state_short(url=self.state_short_url) if self.futures_executor else {"state": {}}
        # short_config's expiry, not self.config's (22 Sep 2026 fix): every
        # real long entry now goes through approve_long_leveraged, which
        # re-validates freshness against short_config's own
        # signal_confirmation_expiry_minutes (10, vs live_config.json's
        # leftover 30 from the retired unleveraged-Spot entry path). A
        # candidate 10-30 minutes old used to pass THIS gather, get
        # attempted, and then be silently rejected as "pending_signal_count"
        # by the inner re-check -- live-reported as "AL adayi buluyor ama
        # AL karari bulamiyor". Gathering with the same config the approval
        # will actually re-check keeps the two in sync.
        long_pending = pending_candidates(saved_long, now_ms, self.short_config)
        short_pending = (pending_short_candidates(saved_short, now_ms, self.short_config)
                         if self.futures_executor else [])
        results = []
        # A candidate that approve_buy/approve_short rejects before placing
        # anything (limit hit, price drifted past max_entry_drift_fraction,
        # already holding the symbol) costs no order and no fresh account
        # scan (pilot_status is cached), so it must not consume the per-tick
        # budget: pending_candidates() is sorted by symbol, and until 18 Sep
        # 2026 taking [:ENTRIES_PER_TICK] meant one persistently-rejected
        # symbol (e.g. its price had moved too far) was retried every tick
        # while every candidate sorted after it was never even looked at.
        taken = 0
        for candidate in long_pending:
            if taken >= self.ENTRIES_PER_TICK:
                break
            if not self._cleared(candidate, gate, now_ms):
                result = self._attempt(candidate, "long", lambda: self._ask_approval(candidate, "long", gate, now_ms))
                self._record(candidate, "long", result)
                results.append({"symbol": candidate["symbol"], "side": "long", "result": result})
                continue
            isolated = isolate_candidate(saved_long, candidate["symbol"])
            update_id = self._order_id(candidate)
            result = self._attempt(candidate, "long",
                                   lambda: self._approve_long(update_id, isolated, tag=self.tag))
            self._record(candidate, "long", result)
            results.append({"symbol": candidate["symbol"], "side": "long", "result": result})
            if result.get("status") == "failed" and _rate_limited():
                break
            if result.get("status") not in ("rejected", "failed"):
                taken += 1
        taken = 0
        for candidate in short_pending:
            if taken >= self.ENTRIES_PER_TICK:
                break
            if not self._cleared(candidate, gate, now_ms):
                result = self._attempt(candidate, "short", lambda: self._ask_approval(candidate, "short", gate, now_ms))
                self._record(candidate, "short", result)
                results.append({"symbol": candidate["symbol"], "side": "short", "result": result})
                continue
            isolated = isolate_short_candidate(saved_short, candidate["symbol"])
            update_id = self._order_id(candidate)
            result = self._attempt(candidate, "short",
                                   lambda: self._approve_short(update_id, isolated, tag=self.tag))
            self._record(candidate, "short", result)
            results.append({"symbol": candidate["symbol"], "side": "short", "result": result})
            if result.get("status") == "failed" and _rate_limited():
                break
            if result.get("status") not in ("rejected", "failed"):
                taken += 1
        return {"status": "auto_entry", "results": results}

    # No Telegram line for these: already handled / between signals, or
    # capacity simply used up (limits, margin) -- expected every window once
    # the strongest candidates are in, so they are /status-only.
    _QUIET_REJECTIONS = {"already_holding_symbol", "pending_signal_count", "signal_expired",
                         "position_limit", "daily_buy_limit", "daily_loss_limit", "pilot_loss_limit",
                         "order is below Binance minimums", "plan exceeds available margin",
                         "awaiting_approval", "already_attempted"}

    # ---- Coins outside the automatic list: ask on Telegram first ----------

    def _auto_symbols(self):
        """(automatic set, full ranked list) from the daily list, or None
        when there is no gate. The list is ranked by 7-day average volume
        (github_worker.prepare_data); the first auto_entry_top_n coins, BTC
        not counted, are bought without asking. An unreadable list asks for
        every coin: fail closed."""
        top_n = (self.short_config or {}).get("auto_entry_top_n")
        if self.manifest_path is None or not top_n:
            return None
        manifest = _read_json(self.manifest_path, {})
        ranked = [s for s in manifest.get("symbols", []) if s != "BTCUSDT"]
        return set(ranked[:int(top_n)]), ranked

    def _cleared(self, candidate, gate, now_ms):
        if gate is None or candidate["symbol"] in gate[0]:
            return True
        with self._approvals_lock:
            request = self._approvals.get(candidate["symbol"])
            return bool(request and request["approved"] and request["created_at"] == candidate["created_at"]
                        and request["expires_ms"] >= now_ms)

    def _forget_expired_approvals(self, now_ms):
        with self._approvals_lock:
            for symbol in [s for s, r in self._approvals.items() if r["expires_ms"] < now_ms]:
                del self._approvals[symbol]

    def _ask_approval(self, candidate, side, gate, now_ms):
        """One Telegram question per candidate, and only when the bot could
        take the trade right now (limits, free slot, coin not held)."""
        symbol = candidate["symbol"]
        with self._approvals_lock:
            request = self._approvals.get(symbol)
            if request and request["created_at"] == candidate["created_at"]:
                return {"status": "rejected", "reason": "awaiting_approval"}
        status = self.futures_market.pilot_status()
        allowed, reason = may_open(dict(self.short_config, live_trading_enabled=True),
                                   status["open_positions"], status["opens_today"],
                                   status["realized_loss_today"], status["pilot_drawdown"],
                                   symbol=symbol, held_symbols=status.get("held_symbols", ()),
                                   equity=status.get("equity"), baseline=status.get("pilot_baseline"))
        if not allowed:
            return {"status": "rejected", "reason": reason}
        expires_ms = candidate["created_at"] + self.short_config["signal_confirmation_expiry_minutes"] * 60_000
        close, stop = Decimal(str(candidate["close"])), Decimal(str(candidate["stop"]))
        distance = abs(close - stop) / close * 100 if close else Decimal("0")
        risk = trade_risk_usdt(self.short_config, status.get("equity"))
        ranked = gate[1]
        rank = ranked.index(symbol) + 1 if symbol in ranked else "?"
        label = f"[{self.tag}] " if self.tag else ""
        minutes = max(1, (expires_ms - now_ms) // 60_000)
        coin = symbol.removesuffix("USDT")
        with self._approvals_lock:
            self._approvals[symbol] = {"created_at": candidate["created_at"], "expires_ms": expires_ms,
                                       "side": side, "approved": False}
        try:
            send_message(f"{label}ONAY GEREKIYOR ({'LONG' if side == 'long' else 'SHORT'}): {symbol} | "
                         f"hacim sirasi {rank} (otomatik: ilk {self.short_config['auto_entry_top_n']}) | "
                         f"sinyal kapanisi {format(close, 'f')} | stop {format(stop, 'f')} (%{distance:.1f}) | "
                         f"risk ~{risk:.2f} USDT\nAlmak icin {minutes} dk icinde yazin: AL {coin}")
        except Exception as exc:
            # Unseen question: forget it so the next tick asks again.
            with self._approvals_lock:
                self._approvals.pop(symbol, None)
            print(f"Telegram approval request failed ({symbol}): {exc}", flush=True)
        return {"status": "rejected", "reason": "awaiting_approval"}

    def approve(self, symbol, now_ms=None):
        """The user's "AL <COIN>" (Telegram polling thread). True when a
        live question for that coin was waiting; the next tick buys it."""
        now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        with self._approvals_lock:
            request = self._approvals.get(symbol)
            if not request or request["expires_ms"] < now_ms:
                return False
            request["approved"] = True
            return True

    def awaiting_approval(self, now_ms=None):
        now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        with self._approvals_lock:
            return sorted(s for s, r in self._approvals.items() if not r["approved"] and r["expires_ms"] >= now_ms)
    _REJECTION_TEXT = {
        "entry_price_moved": "fiyat sinyal kapanisindan izin verilen kaymadan fazla uzaklasti",
        "position_limit": "acik pozisyon limiti dolu",
        "daily_buy_limit": "gunluk islem limiti dolu",
        "daily_loss_limit": "gunluk zarar limiti doldu",
        "pilot_loss_limit": "toplam zarar limiti doldu",
        "stop_too_wide_for_safe_leverage": "stop, guvenli kaldirac icin fazla uzak",
        "order is below Binance minimums": "kalan teminatla Binance minimum islem tutarina ulasilamiyor",
        "risk target below Binance minimum": "islem buyuklugu Binance minimum tutarinin altinda kaliyor; "
                                             "minimuma yuvarlamak riski hedefin ustune cikaracagi icin atlandi",
        "rounded plan exceeds risk budget": "yuvarlama sonrasi risk butcesi asiliyor",
        "plan exceeds available margin": "yeterli teminat yok",
    }

    def _record(self, candidate, side, result):
        status = result.get("status")
        reason = result.get("reason") or result.get("error")
        self.recent_entries.append({"at": time.time(), "symbol": candidate["symbol"], "side": side,
                                    "signal_time": candidate["created_at"], "status": status,
                                    "reason": reason})
        # One Telegram line per candidate (not per tick -- it is retried
        # every loop until its window lapses), so "AL_ADAYI var ama ALDIM
        # yok" is never again a silent mystery. "failed" already alerts
        # from _attempt.
        key = (side, candidate["symbol"], candidate["created_at"])
        if reason == "pilot_loss_limit":
            self._alert_pilot_limit()
        if status != "rejected" or reason in self._QUIET_REJECTIONS or key in self._notified_skips:
            return
        self._notified_skips.add(key)
        label = f"[{self.tag}] " if self.tag else ""
        text = self._REJECTION_TEXT.get(reason, reason)
        try:
            send_message(f"{label}GIRIS YAPILAMADI ({'LONG' if side == 'long' else 'SHORT'}): "
                         f"{candidate['symbol']} | {text}. Sinyal suresi dolana kadar tekrar denenecek.")
        except Exception as exc:
            print(f"Telegram skip notice failed: {exc}", flush=True)

    def _alert_pilot_limit(self):
        """The 40% loss limit used to stop all buying without a word (audit
        A1). One Telegram line per system per UTC day while it holds."""
        day = _utc_day(time.time())
        if self._pilot_alert_day == day:
            return
        self._pilot_alert_day = day
        label = f"[{self.tag}] " if self.tag else ""
        try:
            status = self.futures_market.pilot_status()
            send_message(f"{label}TOPLAM ZARAR SINIRI: bu sistemin sermayesi {status['equity']:.2f} USDT, "
                         f"yatirilan paydaki payi {status['pilot_baseline']:.2f} USDT. Kayip %40 sinirina "
                         "ulasti; yeni alim durdu. Acik pozisyonlar kendi stop ve cikislariyla yonetilmeye devam ediyor.")
        except Exception as exc:
            print(f"Telegram pilot-limit notice failed: {exc}", flush=True)

    def _order_id(self, candidate):
        """tag + per-symbol code + the candidate's own signal time. The
        symbol code is what keeps two candidates of the SAME candle apart:
        without it (until 23 Sep 2026) they shared one client id, so the
        second symbol's protective stop was answered by the first's -- and
        only one of them ever got protected."""
        parts = f"{symbol_code(candidate['symbol'])}{candidate['created_at']}"
        return int(f"{self.tag}{parts}") if self.tag else int(parts)

    def _attempt(self, candidate, side, approve):
        """One candidate's failure must never abort the whole tick's entry
        pass. Live-observed 21 Sep 2026: the first-ever real buy attempts
        all died with -1111 (BAD_PRECISION), and because the exception
        escaped auto_enter, the OTHER 16 pending candidates that tick were
        never even tried and /status showed an empty results list with no
        clue why. A failure is now recorded per candidate (visible in
        /status) and the loop moves on; it does not count against
        ENTRIES_PER_TICK since nothing was opened. A Telegram alert goes
        out only for failures that are NOT a plain Binance rejection
        (OrderRejected means nothing was placed) -- those are the rare,
        possibly-unprotected cases a person should look at now."""
        label = f"[{self.tag}] " if self.tag else ""
        try:
            return approve()
        except ValueError as exc:
            # Planning refused before any order existed (below Binance
            # minimums, plan exceeds risk budget, free capital too small):
            # a rejection like any other, not an incident -- and with a
            # dozen pending candidates it would otherwise re-alert every
            # single tick once the pilot capital is used up.
            print(f"Auto-entry not planned for {candidate['symbol']} ({side}): {exc}", flush=True)
            return {"status": "rejected", "reason": str(exc)}
        except Exception as exc:
            print(f"Auto-entry failed for {candidate['symbol']} ({side}): {exc}", flush=True)
            _note_if_rate_limited(exc)
            if not isinstance(exc, OrderRejected):
                try:
                    send_message(f"{label}GIRIS HATASI: {candidate['symbol']} ({side}) | {exc} | "
                                 "Pozisyon durumunu Binance'te kontrol edin.")
                except Exception as send_exc:
                    print(f"Telegram alert failed: {send_exc}", flush=True)
            return {"status": "failed", "error": str(exc),
                    "code": getattr(exc, "code", None)}

    def telegram(self, payload):
        """A single "AL" takes EVERY signal currently pending -- long and
        short alike -- not just one, per the user's 18 Sep 2026 request:
        most raw breakouts were being thrown away as "ambiguous" simply
        because more than one symbol signaled on the same bar (measured:
        ~97% of raw signals over 30 days). Each candidate is confirmed and
        executed one at a time through the unmodified, already-tested
        approve_buy/approve_short (via isolate_candidate/
        isolate_short_candidate, so each call still sees exactly the one
        candidate it expects) -- existing per-trade safety limits
        (max_open_positions, max_buys_per_day, daily/pilot loss limits,
        available margin) are re-checked fresh before every single one, so
        the loop naturally stops taking new positions once capacity runs
        out rather than needing new capital-tracking logic."""
        parsed = telegram_command(payload, self.environment["TELEGRAM_CHAT_ID"])
        if not parsed: return {"status": "ignored"}
        command = parsed["command"].strip().upper()
        if command != "AL":
            send_message("Komut reddedildi. Yalniz guncel tek AL sinyali icin AL yazin.")
            return {"status": "rejected", "reason": "invalid_command"}
        now_ms = int(time.time() * 1000)
        saved_long = fetch_runtime_state(url=self.state_url)
        saved_short = fetch_runtime_state_short(url=self.state_short_url) if self.futures_executor else {"state": {}}
        # short_config's expiry, matching auto_enter (see its comment):
        # approve_long_leveraged always re-validates against short_config,
        # never self.config.
        long_pending = pending_candidates(saved_long, now_ms, self.short_config)
        short_pending = (pending_short_candidates(saved_short, now_ms, self.short_config)
                        if self.futures_executor else [])
        if not long_pending and not short_pending:
            send_message("AL yapilmadi: su an bekleyen bir sinyal yok.")
            return {"status": "rejected", "reason": "no_pending_signal"}
        base_id = parsed["update_id"] * 1000
        # Same tag prefix auto_enter uses: live_positions()/pilot_status()
        # attribute an order to a system by the digit right after the 5-char
        # prefix (_belongs_to_tag), so an untagged Telegram-triggered order
        # belonged to neither app and its position was invisible to every
        # exit scan -- only the exchange-native stop still covered it.
        def order_id(i):
            return int(f"{self.tag}{base_id + i}") if self.tag else base_id + i
        results = []
        for i, candidate in enumerate(long_pending):
            isolated = isolate_candidate(saved_long, candidate["symbol"])
            results.append({"symbol": candidate["symbol"], "side": "long",
                            "result": self._approve_long(order_id(i), isolated, tag=self.tag)})
        for i, candidate in enumerate(short_pending, start=len(long_pending)):
            isolated = isolate_short_candidate(saved_short, candidate["symbol"])
            results.append({"symbol": candidate["symbol"], "side": "short",
                            "result": self._approve_short(order_id(i), isolated, tag=self.tag)})
        return {"status": "batch", "results": results}

    def scan(self):
        """Spot and futures sides are checked in their own try/except so a
        Binance error on one side (e.g. a transient -1003 rate limit) can
        never suppress the exit check on the other -- exits are the one
        thing that must never silently stop running. Each side still
        raises internally to its own except block rather than being
        swallowed per-position, since a mid-loop exception here (as
        opposed to inside a single position's exit decision) means
        live_positions()/analysis() itself is failing, at which point
        there is nothing further to safely check on that side anyway."""
        results = []
        # The trend-exit check MUST run under the same btc_filter mode that
        # opened the position (config_v5_long.json: "very_loose"). Passing
        # the bare ShortWindowLongModel.sell (called as sell_fn(feature,
        # btc), config=None) silently fell back to long_exit's "strict"
        # default -- the exact mismatch research_v5.long_exit's own comment
        # warns about: with BTC merely above EMA20 (a valid very_loose
        # entry) but not in a full EMA20>50>200 stack, the strict check
        # fails on the very first scan after entry and the position is
        # force-closed 5 minutes after it opened. Found in the 22 Sep 2026
        # code review before any leveraged long had gone through this path.
        spot_sell = lambda f, b: ShortWindowLongModel.sell(f, b, self.market.strategy_config)
        try:
            for position in self.market.live_positions():
                feature, btc, high = self.market.analysis(position, ShortWindowLongModel.features, self.interval)
                reason = exit_decision(position, feature, btc, high,
                                       {**self.config, **{"trailing_atr": self.market.strategy_config["trailing_atr"]}},
                                       sell_fn=spot_sell)
                if not reason: continue
                if not execution_enabled(self.config, self.environment):
                    results.append({"status": "preview_exit", "symbol": position["symbol"], "reason": reason})
                    continue
                result = execute_exit(position, reason, self.executor)
                pnl = _fill_pnl(position["entry"], result.get("order"), "long")
                send_message(f"SATTIM: {position['symbol']}{_pnl_suffix(pnl)} | Neden: {reason}")
                results.append(result)
        except Exception as exc:
            print(f"Spot exit scan failed: {exc}", flush=True)
            results.append({"status": "scan_failed", "side": "spot", "error": str(exc)})
            _note_if_rate_limited(exc)
        if self.futures_market:
            try:
                # live_positions() now returns BOTH short and leveraged-long
                # positions (21 Sep 2026), each tagged with its own "side" --
                # dispatch to the matching decision/execution pair. The long
                # exit reuses live_monitor.exit_decision verbatim (identical
                # math to Spot's own long exit, since it's the same v5
                # signal) with cap_at_target forced False regardless of what
                # short_live_config.json happens to have, to match the
                # walk-forward-tested "let winners run" behavior (config_v5_
                # long.json/live_config.json set this explicitly for Spot;
                # short_live_config.json has no such key at all, and
                # exit_decision's own default is True/capped, which would
                # silently diverge from what was actually validated).
                futures_sell = lambda f, b: ShortWindowLongModel.sell(f, b, self.futures_market.strategy_config)
                for position in self.futures_market.live_positions():
                    try:
                        results.extend(self._manage_futures_position(position, futures_sell))
                    except Exception as exc:
                        # One position's failure must not stop the others from
                        # being protected and checked (24 Sep 2026: one bad
                        # stop aborted the scan for every position, each tick).
                        print(f"Futures exit scan failed ({position['symbol']}): {exc}", flush=True)
                        results.append({"status": "scan_failed", "side": "futures",
                                        "symbol": position["symbol"], "error": str(exc)})
                        _note_if_rate_limited(exc)
                for orphan in list(getattr(self.futures_market, "orphan_stops", None) or []):
                    try:
                        results.extend(self._clear_orphan(orphan))
                    except Exception as exc:
                        print(f"Orphan stop cleanup failed ({orphan['symbol']}): {exc}", flush=True)
                        _note_if_rate_limited(exc)
            except Exception as exc:
                print(f"Futures exit scan failed: {exc}", flush=True)
                results.append({"status": "scan_failed", "side": "futures", "error": str(exc)})
                _note_if_rate_limited(exc)
        self._watch_scan_failures(results)
        return {"status": "scanned", "results": results}

    # Consecutive failed exit scans before the user is told (a loop is ~2
    # minutes, so ~10 minutes). One bad tick is normal network noise.
    SCAN_FAILURE_ALERT = 5

    def _watch_scan_failures(self, results):
        """On 24 Sep 2026 the exit scan failed on every tick for 17 minutes
        and only the log knew (audit C2). A Telegram line once it has failed
        SCAN_FAILURE_ALERT times in a row, and one when it recovers."""
        failed = [r for r in results if r.get("status") == "scan_failed"]
        label = f"[{self.tag}] " if self.tag else ""
        if not failed:
            if self._scan_failures >= self.SCAN_FAILURE_ALERT:
                try:
                    send_message(f"{label}DUZELDI: cikis kontrolu yeniden calisiyor.")
                except Exception as exc:
                    print(f"Telegram recovery notice failed: {exc}", flush=True)
            self._scan_failures = 0
            return
        self._scan_failures += 1
        if self._scan_failures == self.SCAN_FAILURE_ALERT:
            try:
                send_message(f"{label}UYARI: cikis kontrolu {self._scan_failures} turdur hata veriyor "
                             f"({failed[0].get('symbol') or failed[0].get('side')}: {failed[0].get('error')}). "
                             "Stoplar Binance'te duruyor; iz suren cikislar calismiyor olabilir.")
            except Exception as exc:
                print(f"Telegram scan-failure notice failed: {exc}", flush=True)

    def _manage_futures_position(self, position, futures_sell):
        """Protect or ratchet, then check the exit, for one futures position.
        Returns the list of what happened (empty when nothing did)."""
        results = []
        if position.get("extra_stop_ids") and execution_enabled(self.short_config, self.environment):
            # A replaced stop whose cancel did not go through: the position's
            # own stop is the tighter one, so the extra goes.
            for extra in position["extra_stop_ids"]:
                cancel_quietly(self.futures_executor, position["symbol"], extra)
            results.append({"status": "extra_stops_cancelled", "symbol": position["symbol"],
                            "stops": list(position["extra_stop_ids"])})
        if position["side"] == "short":
            feature, btc, extreme = self.futures_market.analysis(position, symmetric_features, self.interval)
        else:
            feature, btc, extreme = self.futures_market.analysis(position, ShortWindowLongModel.features, self.interval)
        breached = False
        if position.get("unprotected"):
            protected = self._protect(position, feature, extreme)
            results.append(protected)
            # The price is already past where the lost stop would have sold
            # (audit A3): no stop can be placed there any more, and the exit
            # rules below never compare the price with the stop, so until 25
            # Sep 2026 such a position just sat with nothing but liquidation
            # behind it. The stop would have closed it; close it now.
            breached = protected.get("reason") == "stop_already_breached"
        else:
            moved = self._ratchet(position, feature, extreme)
            if moved:
                results.append(moved)
        judged = _decision_view(position)
        if breached:
            reason = "stop_loss"
        elif position["side"] == "short":
            reason = short_exit_decision(judged, feature, btc, extreme, self.short_config)
        else:
            reason = exit_decision(judged, feature, btc, extreme,
                                   {**self.short_config,
                                    "trailing_atr": self.futures_market.strategy_config["trailing_atr"],
                                    "cap_at_target": False},
                                   sell_fn=futures_sell)
        if not reason:
            return results
        if not execution_enabled(self.short_config, self.environment):
            results.append({"status": "preview_exit", "symbol": position["symbol"], "reason": reason})
            return results
        if position["side"] == "short":
            result = execute_short_exit(position, reason, self.futures_executor)
            pnl = _fill_pnl(position["entry"], result.get("order"), "short")
            send_message(f"SATTIM (SHORT KAPANDI): {position['symbol']}{_pnl_suffix(pnl)} | Neden: {reason}")
        else:
            result = execute_long_futures_exit(position, reason, self.futures_executor)
            pnl = _fill_pnl(position["entry"], result.get("order"), "long")
            send_message(f"SATTIM (KALDIRACLI KAPANDI): {position['symbol']}{_pnl_suffix(pnl)} | Neden: {reason}")
        results.append(result)
        return results

    def _resting_distance(self, atr):
        """How far behind the best price the RESTING stop trails: the tested
        trail distance times resting_stop_trail_multiple (2 live, 24 Sep 2026).
        The real trailing exit is exit_decision's close-based market exit; on
        3 years of data that beat a resting stop at the trail distance itself
        (6.98 -> 7.77 R/week, and in every random candidate ordering), because
        intrabar wicks no longer shake winners out. The wide resting stop is
        the safety net for while this process is down."""
        trailing = Decimal(str(self.futures_market.strategy_config["trailing_atr"]))
        multiple = Decimal(str((self.short_config or {}).get("resting_stop_trail_multiple", 1)))
        return trailing * multiple * atr

    def _protect(self, position, feature, extreme=None):
        """Place the missing protective stop on a position that has none
        (live_short_market.unprotected_positions). Needed because a stop can
        fail to rest while the position itself is real: every one did on 22
        Sep 2026 (Binance -4120), and a crash between the open and its stop
        does the same.

        The level: the lost stop's own level when Binance still knows it (a
        fresh entry-style ATR stop only when it does not), lifted to the
        resting trail when the trade has run. Until 24 Sep 2026 it was always
        close - 2 ATR, which loosened a losing trade's stop and squeezed a
        winner's trail to 2 ATR.

        Sets stop_price either way, so an already-breached stop still gives
        the exit check something to act on (it closes the position this same
        tick) instead of leaving it with no reference price at all."""
        symbol, side = position["symbol"], position["side"]
        atr = Decimal(str(feature["atr"]))
        close = Decimal(str(feature["c"]))
        multiplier = Decimal(str(self.futures_market.strategy_config["atr_multiplier"]))
        rules = self.futures_market.rules(symbol)
        stop_id = position["stop_client_id"]
        try:
            previous = self.futures_executor.query(symbol, stop_id)
        except OrderRejected as error:
            if error.code != -2013:  # Binance: order does not exist.
                raise
            previous = None
        if previous is not None and previous.get("status") == "NEW" and Decimal(str(previous.get("stopPrice") or "0")) > 0:
            # It does rest -- the open-orders list was a moment behind.
            position["stop_price"] = Decimal(str(previous["stopPrice"]))
            return {"status": "already_protected", "symbol": symbol, "order": previous}
        level = Decimal(str((previous or {}).get("stopPrice") or "0"))
        if level <= 0:
            level = close - multiplier * atr if side == "long" else close + multiplier * atr
        if extreme is not None and "trailing_atr" in self.futures_market.strategy_config:
            best = Decimal(str(extreme))
            if side == "long":
                level = max(level, best - self._resting_distance(atr))
            else:
                level = min(level, best + self._resting_distance(atr))
        stop = _down(level, rules["tick_size"]) if side == "long" else _up(level, rules["tick_size"])
        position["stop_price"] = stop
        price = Decimal(str(self.futures_market.price(symbol)))
        breached = stop >= price if side == "long" else stop <= price
        if breached:
            return {"status": "protect_skipped", "symbol": symbol, "reason": "stop_already_breached"}
        if not execution_enabled(self.short_config, self.environment):
            return {"status": "protect_skipped", "symbol": symbol, "reason": "real_orders_disabled"}
        place = (self.futures_executor.protective_stop_for_long if side == "long"
                 else self.futures_executor.protective_stop_for_short)
        if previous is None:
            order = _known_or_place(self.futures_executor, symbol, stop_id,
                                    lambda: place(symbol, position["quantity"], format(stop, "f"), stop_id))
        else:
            # That id is an OLD stop, already gone (cancelled by a ratchet or
            # an exit that then failed) -- or in a state we do not know, which
            # is not trusted as resting either. Treating it as "already
            # placed" left four positions with no stop at all on 24 Sep 2026.
            stop_id = _fresh_stop_id(stop_id)
            order = place(symbol, position["quantity"], format(stop, "f"), stop_id)
        position["stop_client_id"] = stop_id
        label = f"[{self.tag}] " if self.tag else ""
        try:
            send_message(f"{label}KORUMA EKLENDI: {symbol} ({side}) | stop {format(stop, 'f')} | "
                         "korumasiz kalan pozisyona koruyucu emir kuruldu.")
        except Exception as exc:
            print(f"Telegram protect notice failed: {exc}", flush=True)
        return {"status": "protected", "symbol": symbol, "stop_price": format(stop, "f"), "order": order}

    # A resting stop is only moved when the new level is at least this much
    # better, so ordinary noise does not cancel/replace an order every tick.
    RATCHET_MIN_IMPROVEMENT = Decimal("0.003")

    def _ratchet(self, position, feature, extreme):
        """Move the RESTING protective stop in the profitable direction as
        the trade works: best price since entry -/+ the resting distance (see
        _resting_distance), armed once the trade is 1R in front. It never
        loosens a stop and never moves it past the current price.

        Two fixes on 24 Sep 2026:
        - A stop already at or past entry counts as armed. Before, it made
          "risk" zero or negative and this returned early, so the resting stop
          froze at about breakeven for every big winner.
        - The new stop is placed BEFORE the old one is cancelled, so there is
          never a moment without one. If the placement fails, the old stop
          simply stays. If the cancel fails, both rest (reduce-only, harmless)
          and live_positions hands the looser one back for cancelling."""
        stop_id = position.get("stop_client_id")
        if not stop_id or position.get("stop_price") is None:
            return None
        side = position["side"]
        entry, stop = Decimal(str(position["entry"])), Decimal(str(position["stop_price"]))
        atr = Decimal(str(feature["atr"]))
        best = Decimal(str(extreme))
        if atr <= 0:
            return None
        risk = entry - stop if side == "long" else stop - entry
        armed = risk <= 0 or (best >= entry + risk if side == "long" else best <= entry - risk)
        if not armed:
            return None
        distance = self._resting_distance(atr)
        rules = self.futures_market.rules(position["symbol"])
        moved = (_down(best - distance, rules["tick_size"]) if side == "long"
                 else _up(best + distance, rules["tick_size"]))
        if side == "long":
            threshold = stop * (Decimal("1") + self.RATCHET_MIN_IMPROVEMENT)
        else:
            threshold = stop * (Decimal("1") - self.RATCHET_MIN_IMPROVEMENT)
        if (moved <= threshold) if side == "long" else (moved >= threshold):
            return None
        price = Decimal(str(self.futures_market.price(position["symbol"])))
        if (moved >= price) if side == "long" else (moved <= price):
            return None  # already breached: the exit check closes it instead
        if not execution_enabled(self.short_config, self.environment):
            return None
        new_id = _fresh_stop_id(stop_id)
        place = (self.futures_executor.protective_stop_for_long if side == "long"
                 else self.futures_executor.protective_stop_for_short)
        order = place(position["symbol"], position["quantity"], format(moved, "f"), new_id)
        position["stop_price"], position["stop_client_id"] = moved, new_id
        cancel_quietly(self.futures_executor, position["symbol"], stop_id)
        label = f"[{self.tag}] " if self.tag else ""
        try:
            send_message(f"{label}STOP YUKSELTILDI: {position['symbol']} ({side}) | "
                         f"{format(stop, 'f')} -> {format(moved, 'f')}")
        except Exception as exc:
            print(f"Telegram ratchet notice failed: {exc}", flush=True)
        return {"status": "stop_moved", "symbol": position["symbol"],
                "from": format(stop, "f"), "to": format(moved, "f"), "order": order}

    def _clear_orphan(self, orphan):
        """Cancel the stop(s) of a position that is no longer open -- after a
        fresh check, since the list that flagged it can be 90 s old."""
        if not execution_enabled(self.short_config, self.environment):
            return []
        if open_amount(self.futures_executor, orphan["symbol"], orphan["side"]) > 0:
            return []
        for stop_id in orphan["stop_ids"]:
            cancel_quietly(self.futures_executor, orphan["symbol"], stop_id)
        print(f"Orphan stop removed: {orphan['symbol']} ({', '.join(orphan['stop_ids'])})", flush=True)
        return [{"status": "orphan_stop_cancelled", "symbol": orphan["symbol"], "stops": orphan["stop_ids"]}]

    # ---- The simulation's daily loss rule, live -------------------------

    def _load_guard(self):
        if self._guard is None and self.guard_path is not None:
            self._guard = _read_json(self.guard_path, None)
        return self._guard

    def _save_guard(self):
        if self.guard_path is None:
            return
        try:
            _write_json(self.guard_path, self._guard)
        except OSError as exc:
            print(f"Daily equity stop state not saved: {exc}", flush=True)

    def halted_today(self, now_s=None):
        guard = self._load_guard()
        day = _utc_day(time.time() if now_s is None else now_s)
        return bool(guard and guard.get("day") == day and guard.get("halted"))

    def daily_guard(self):
        """The research simulation closes every position and stops buying
        for the rest of the UTC day once a system is down
        daily_equity_stop_fraction (10%) on the day; the live bot never did,
        so its worst drawdown could reach 45-46% instead of the tested ~38%,
        and the 40% pilot limit would then have stopped the bot at the bottom
        (user's decision, 24 Sep 2026: add the rule).

        Measured on this system's own trades only: today's realized result
        plus the change in its open positions' result since the day began --
        hand trades, the other system and transfers do not count. Checked once
        per bar of this system, as the simulation does, not on every wick."""
        fraction = (self.short_config or {}).get("daily_equity_stop_fraction")
        if not fraction or not self.futures_market:
            return None
        now = time.time()
        day = _utc_day(now)
        guard = self._load_guard()
        if not guard or guard.get("day") != day:
            status = self.futures_market.pilot_status()
            self._guard = {"day": day, "equity": str(status["equity"]),
                           "unrealized": str(status.get("own_unrealized", 0)),
                           "unrealized_by_symbol": {k: str(v) for k, v in
                                                    (status.get("own_unrealized_by_symbol") or {}).items()},
                           "halted": False, "bar": None}
            self._save_guard()
            return None
        if guard.get("halted"):
            return self._close_all("daily_loss_limit")  # retries anything a failed close left open
        bar = int(now * 1000) // self.interval
        if guard.get("bar") == bar:
            return None
        status = self.futures_market.pilot_status()
        guard["bar"] = bar
        equity = Decimal(guard["equity"])
        change = (Decimal(str(status.get("realized_pnl_today", 0))) + Decimal(str(status.get("own_unrealized", 0)))
                  - Decimal(guard["unrealized"]) + self._closed_elsewhere(guard, status, now))
        if equity <= 0 or change > -Decimal(str(fraction)) * equity:
            self._save_guard()
            return None
        guard["halted"], guard["change"] = True, str(change)
        self._save_guard()
        label = f"[{self.tag}] " if self.tag else ""
        try:
            send_message(f"{label}GUNLUK ZARAR KURALI: bu sistem bugun {change:.2f} USDT "
                         f"(%{change / equity * 100:.1f}) geride. Tum pozisyonlari kapatiyorum; "
                         "03:00'e (UTC gun sonu) kadar yeni giris yok.")
        except Exception as exc:
            print(f"Telegram daily stop notice failed: {exc}", flush=True)
        return self._close_all("daily_loss_limit")

    def _closed_elsewhere(self, guard, status, now):
        """Today's result of this system's positions that closed without an
        order of ours -- by hand, nearly always (audit A2). Their open result
        at the day's start is in the baseline, but they are no longer open
        and no close of ours booked them: until 25 Sep 2026 a winner closed
        by hand read as a loss of its whole open profit (had SAGA's +12 USDT
        been closed two hours later, the 4H side would have read -12 and sold
        NIL, OP and LINK), and a loser closed by hand did not count at all.
        Binance's own realized P&L and fees for those coins since the day
        began stand in for the close we did not make."""
        seen = set(guard.get("unrealized_by_symbol") or {}) | set(status.get("opened_symbols_today") or ())
        gone = (seen - set(status.get("held_symbols") or ()) - set(status.get("own_unrealized_by_symbol") or {})
                - set(status.get("closed_symbols_today") or ()))
        if not gone:
            return Decimal("0")
        day_start_ms = int(datetime.strptime(guard["day"], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
        rows = self.futures_executor.income(day_start_ms, int(now * 1000))
        return sum((Decimal(str(r.get("income", "0"))) for r in rows
                    if r.get("symbol") in gone and r.get("incomeType") in ("REALIZED_PNL", "COMMISSION")),
                   Decimal("0"))

    def _close_all(self, reason):
        if not execution_enabled(self.short_config, self.environment):
            return {"status": "daily_stop_preview"}
        results = []
        for position in self.futures_market.live_positions():
            side = position["side"]
            try:
                execute = execute_short_exit if side == "short" else execute_long_futures_exit
                result = execute(position, reason, self.futures_executor)
                if result.get("status") == "closed":
                    pnl = _fill_pnl(position["entry"], result.get("order"), side)
                    send_message(f"SATTIM ({'SHORT' if side == 'short' else 'KALDIRACLI'} KAPANDI): "
                                 f"{position['symbol']}{_pnl_suffix(pnl)} | Neden: gunluk zarar kurali")
                results.append(result)
            except Exception as exc:
                print(f"Daily stop close failed ({position['symbol']}): {exc}", flush=True)
                results.append({"status": "failed", "symbol": position["symbol"], "error": str(exc)})
                _note_if_rate_limited(exc)
        return {"status": "daily_stop", "results": results}

    def tick(self, entries=True, exits=True):
        """One cycle: the daily equity stop and the exit scan first, then any
        pending entry. The periodic loop runs the exit half before candidate
        detection and the entry half after it (see run_periodic_scans), so a
        bar-close exit no longer waits for detection and entries; /scan runs
        both.

        Skips entirely while the process-wide Binance rate-limit cooldown
        (see _rate_limited/_note_if_rate_limited) is active: every call
        would fail anyway during a real -1003 ban, so retrying only wastes
        the retry budget and risks the ban treating each attempt as a
        further violation. The exchange-native protective stop placed at
        entry time does not depend on this loop running -- it is a real
        resting order on Binance regardless of whether we can poll it."""
        # The periodic thread and an external /scan (GitHub Actions' wake-up
        # ping, or a person opening the URL) can call this at the same
        # instant. Two concurrent ticks would evaluate the same pending
        # candidate twice: _known_or_place's query-before-place makes a
        # FILLED buy idempotent once it exists, but not while both are still
        # between "query said nothing" and "place" -- and Binance only
        # enforces client-id uniqueness among OPEN orders, so a second
        # market buy would go through. Non-blocking: the late caller just
        # reports busy instead of queueing up a redundant tick.
        if not self._tick_lock.acquire(blocking=False):
            return {"status": "busy", "entries": {"status": "skipped"}, "exits": {"status": "skipped"}}
        try:
            result = self._tick(entries, exits)
        except Exception as exc:
            self.last_tick = {"at": time.time(), "result": {"status": "failed", "error": str(exc)}}
            raise
        finally:
            self._tick_lock.release()
        self.last_tick = {"at": time.time(), "result": result}
        return result

    def _tick(self, entries=True, exits=True):
        if _rate_limited():
            return {"status": "cooling_down", "entries": {"status": "skipped"}, "exits": {"status": "skipped"}}
        hits_before = _consecutive_rate_limit_hits
        guard, exit_result = None, {"status": "skipped"}
        if exits:
            try:
                guard = self.daily_guard()
            except Exception as exc:
                print(f"Daily equity stop check failed: {exc}", flush=True)
                _note_if_rate_limited(exc)
            exit_result = self.scan()
        entry_result = {"status": "skipped"}
        if entries:
            entry_result = {"status": "auto_entry", "results": []}
            try:
                entry_result = self.auto_enter()
            except Exception as exc:
                print(f"Auto-entry failed: {exc}", flush=True)
                _note_if_rate_limited(exc)
        # A tick that ran to completion without a FRESH -1003 (the counter
        # is unchanged from before this tick) means Binance is responding
        # normally again -- reset the escalation so the next real ban
        # starts probing at the short 5-minute cooldown again, not wherever
        # a previous, unrelated ban left off.
        if _consecutive_rate_limit_hits == hits_before:
            _note_rate_limit_cleared()
        result = {"entries": entry_result, "exits": exit_result}
        if guard:
            result["daily_guard"] = guard
        return result

def status_snapshot(apps=None, now=time.time):
    """Read-only view for GET /status: the real-order switch, the shared
    rate-limit breaker, and each app's last tick verbatim. Unlike /scan it
    performs no tick and touches Binance not at all. Added 18 Sep 2026
    after a day of diagnosing "AL_ADAYI arrived, ALDIM never did" blind:
    approve_buy's rejections are deliberately silent on Telegram (they
    recur every cycle), so this is the one place their reason is visible."""
    apps = APPS if apps is None else apps
    return {
        "orders_enabled": STATUS["orders_enabled"],
        "commit": STATUS["commit"],
        "rate_limit": {
            "cooling_down": now() < _rate_limited_until,
            "cooldown_ends_in_s": max(0, int(_rate_limited_until - now())),
            "consecutive_hits": _consecutive_rate_limit_hits,
            "last_message": _rate_limit_last_message,
        },
        "apps": {app.tag or "default": app.last_tick for app in apps},
        "recent_entries": {app.tag or "default": list(getattr(app, "recent_entries", ())) for app in apps},
    }


def _position_view(position, current_price, side):
    """entry/stop/quantity come straight off the exchange-native fill and
    protective stop order (live_market.BinanceMarket.live_positions / the
    futures mirror) -- never re-derived locally -- so this is exactly what
    a person would see checking Binance directly, just pre-computed."""
    entry = Decimal(str(position["entry"]))
    stop = Decimal(str(position["stop_price"]))
    qty = Decimal(str(position["quantity"]))
    price = Decimal(str(current_price))
    signed = (price - entry) if side == "long" else (entry - price)
    stop_signed = (price - stop) if side == "long" else (stop - price)
    return {
        "symbol": position["symbol"],
        "side": side,
        "quantity": str(qty),
        "entry_price": str(entry),
        "current_price": str(price),
        "stop_price": str(stop),
        "cost_usdt": str(qty * entry),
        "current_value_usdt": str(qty * price),
        "unrealized_pnl_usdt": str(qty * signed),
        "unrealized_pnl_pct": f"{(signed / entry * 100):.2f}" if entry else "0",
        "distance_to_stop_pct": f"{(stop_signed / price * 100):.2f}" if price else "0",
    }


def positions_snapshot(apps=None):
    """Read-only GET /positions: real quantity, entry, stop and live
    unrealized P&L per open position, straight from Binance (a signed
    account/order read, unlike /status which touches nothing). Added 21
    Sep 2026 the first day real fills existed to answer -- once entries
    stopped silently failing -- the very next question: how much did it
    actually buy, where is the stop, what is it worth right now."""
    apps = APPS if apps is None else apps
    result = {}
    for app in apps:
        spot, futures = [], []
        for position in app.market.live_positions():
            price = app.market.price(position["symbol"])
            spot.append(_position_view(position, price, "long"))
        if app.futures_market:
            for position in app.futures_market.live_positions():
                price = app.futures_market.price(position["symbol"])
                # Futures holds leveraged longs too (21 Sep 2026); a long
                # reported as "short" here showed every sign flipped.
                futures.append(_position_view(position, price, position.get("side", "short")))
        result[app.tag or "default"] = {"spot": spot, "futures": futures}
    return result


# 2 minutes, down from 5 (22 Sep 2026): with detection now running once
# per candle window instead of every loop (github_worker.local_tick), a
# loop is cheap, and a candidate gets ~12 entry attempts inside its
# 30-minute window instead of 1-2. pilot_status stays cached for 90s.
LOOP_SECONDS = 120


def run_periodic_scans(apps, interval_seconds=LOOP_SECONDS, sleep=time.sleep, max_iterations=None, detect=None,
                       housekeeping=None):
    """Independent of GitHub Actions' free-tier cron, whose scheduled runs
    have been observed to lag by hours rather than minutes. Runs only
    while this Render process is warm; a cold free-tier instance still
    needs an external request (health check, webhook, cron) to wake it,
    but does not depend on that request landing on any particular schedule
    to keep ticking once awake. The exchange-native protective stop placed
    at entry time (live_controller.approve_buy) does not depend on this
    loop at all -- it is a real resting order on Binance regardless.
    `apps` is a list so the 4H and 2H systems (18 Sep 2026) both get
    ticked every cycle from one loop/thread; a single app's failure (try/
    except per app, not around the whole list) never blocks the other.

    `detect`, when given, is a zero-arg callable (github_worker.local_tick
    bound to a runtime dir) run once per iteration BEFORE the apps tick --
    candidate detection itself was still solely GitHub-Actions-gated even
    after entries/exits moved to this loop (21 Sep 2026), which meant a
    candidate could be discovered hours late and already past its freshness
    window. Called from this same loop/thread, immediately before the apps
    read the files it just wrote, so there is never a concurrent read of a
    half-written file. A detect() failure is caught here, same as a single
    app's tick failure, and never stops the apps from ticking -- entries/
    exits on already-known candidates/positions must keep working even if
    one detection pass failed (a transient network error fetching candles,
    say)."""
    if not isinstance(apps, (list, tuple)):
        apps = [apps]

    def tick_all(**phase):
        for app in apps:
            LOOP_PROGRESS["at"] = time.time()
            try:
                app.tick(**phase)
            except Exception as exc:
                print(f"Periodic tick failed: {exc}", flush=True)

    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        LOOP_PROGRESS["at"] = time.time()
        # Exits first (24 Sep 2026): at a bar close the detection pass can
        # take a minute or more, and the exits used to wait for it and for
        # the entries.
        tick_all(entries=False)
        if detect is not None:
            LOOP_PROGRESS["at"] = time.time()
            try:
                detect()
            except Exception as exc:
                print(f"Local candidate detection failed: {exc}", flush=True)
        tick_all(exits=False)
        if housekeeping is not None:
            # Non-trading chores (the trade ledger): never allowed to break
            # or delay what the loop is for beyond one caught exception.
            try:
                housekeeping()
            except Exception as exc:
                print(f"Housekeeping failed: {exc}", flush=True)
        iterations += 1
        LOOP_PROGRESS["at"] = time.time()
        sleep(interval_seconds)


# Last time the periodic loop made progress. Live 23 Sep 2026 the loop sat
# for 6+ minutes on one Binance connection that neither answered nor timed
# out (no CPU, one ESTABLISHED socket): nothing was being entered, exited or
# re-protected, and nobody would have known. See watch_loop.
LOOP_PROGRESS = {"at": time.time()}
WATCHDOG_SECONDS = 15 * 60  # a 2H detection pass can take ~7 min on a slow link


def watch_loop(limit=WATCHDOG_SECONDS, check_every=30, now=time.time, sleep=time.sleep,
               dump=None, exit_process=None):
    """Exit the whole process when the periodic loop has made no progress
    for `limit` seconds, after writing every thread's stack to the log (so
    the next occurrence shows exactly which call hung). The launcher starts
    the bot again; the exchange-resting stops never depended on this
    process. A thread cannot be killed in Python, so exiting is the only
    reliable way out of a hung socket."""
    import faulthandler
    log_path = Path(os.environ.get("CRYPTO_STORAGE", "runtime")) / "bot.log"
    def dump_to_log():
        # Straight to the file: sys.stderr may be the very console that is
        # frozen, which is exactly how the 23 Sep 16:21 stall went unlogged.
        with open(log_path, "a", encoding="utf-8") as log:
            log.write(time.strftime("%Y-%m-%d %H:%M:%S") + " WATCHDOG thread dump:\n")
            log.flush()
            faulthandler.dump_traceback(file=log, all_threads=True)
    dump = dump or dump_to_log
    exit_process = exit_process or (lambda: os._exit(3))
    while True:
        sleep(check_every)
        stalled = now() - LOOP_PROGRESS["at"]
        if stalled > limit:
            try:
                with open(Path(os.environ.get("CRYPTO_STORAGE", "runtime")) / "bot.log", "a", encoding="utf-8") as log:
                    log.write(time.strftime("%Y-%m-%d %H:%M:%S") + f" WATCHDOG: periodic loop stalled for "
                              f"{int(stalled)} s; dumping threads and exiting so the launcher restarts the bot.\n")
            except OSError:
                pass
            try:
                dump()
                # Nobody used to hear of it (audit C2); the bot is down
                # until the launcher has it up again.
                try:
                    send_message(f"DONMA: bot {int(stalled) // 60} dakikadir ilerlemiyor; kendini kapatip "
                                 "yeniden baslatiyor. Stoplar Binance'te duruyor.")
                except Exception as exc:
                    print(f"Telegram stall notice failed: {exc}", flush=True)
            finally:
                exit_process()
            return


START_NOTICE_SECONDS = 600


def announce_start(runtime_dir, commit, now=time.time):
    """A Telegram line when the bot starts (audit C2): after a crash, a
    freeze or a reboot, the user learns it is back. At most one per 10
    minutes, so a startup crash loop cannot flood the chat; the restarts in
    between are counted into the next one."""
    path = Path(runtime_dir) / "starts.json"
    state = _read_json(path, {}) or {}
    suppressed = int(state.get("suppressed", 0))
    if now() - float(state.get("announced_at", 0)) < START_NOTICE_SECONDS:
        state["suppressed"] = suppressed + 1
        _write_json(path, state)
        return False
    text = f"BULUTLARIN EFENDISI CALISIYOR (surum {str(commit)[:7]})."
    if suppressed:
        text += f" Son bildirimden beri {suppressed} kez daha yeniden basladi; bot.log incelenmeli."
    try:
        send_message(text)
    except Exception as exc:
        print(f"Telegram start notice failed: {exc}", flush=True)
        return False
    _write_json(path, {"announced_at": now(), "suppressed": 0})
    return True


SUMMARY_HOUR_UTC = 6  # 09:00 Turkey


def daily_summary(executor, market, runtime_dir, now=time.time):
    """One morning message (audit C2): the balance against the money put in,
    what is open and how it stands, and what the last 24 hours realized.
    Once per UTC day, from SUMMARY_HOUR_UTC on; remembered on disk so a
    restart does not send it again."""
    moment = datetime.fromtimestamp(now(), timezone.utc)
    day = moment.date().isoformat()
    path = Path(runtime_dir) / "summary.json"
    if moment.hour < SUMMARY_HOUR_UTC or (_read_json(path, {}) or {}).get("day") == day:
        return False
    account = executor.account()
    balance = Decimal(str(account.get("totalMarginBalance") or "0"))
    deposits = market.net_deposits() if market is not None else None
    open_positions = sorted(((p["symbol"].removesuffix("USDT"), Decimal(str(p.get("unrealizedProfit") or "0")))
                             for p in account.get("positions", []) if Decimal(str(p.get("positionAmt") or "0")) != 0),
                            key=lambda x: x[1], reverse=True)
    end_ms = int(now() * 1000)
    rows = executor.income(end_ms - 86_400_000, end_ms)
    realized = sum((Decimal(str(r.get("income", "0"))) for r in rows
                    if r.get("incomeType") in ("REALIZED_PNL", "COMMISSION", "FUNDING_FEE")), Decimal("0"))
    lines = [f"GUNLUK OZET: bakiye {balance:.2f} USDT"]
    if deposits:
        result = balance - deposits
        lines[0] += f" | yatirilan {deposits:.2f} -> {result:+.2f} USDT (%{result / deposits * 100:+.1f})"
    lines.append(f"Son 24 saatte gerceklesen: {realized:+.2f} USDT")
    if open_positions:
        total = sum((u for _, u in open_positions), Decimal("0"))
        lines.append(f"Acik {len(open_positions)} pozisyon, acik K/Z {total:+.2f} USDT: "
                     + ", ".join(f"{s} {u:+.2f}" for s, u in open_positions))
    else:
        lines.append("Acik pozisyon yok.")
    send_message("\n".join(lines))
    _write_json(path, {"day": day})
    return True


class Handler(BaseHTTPRequestHandler):
    def _json(self, status, value):
        body = json.dumps(value, default=str).encode(); self.send_response(status)
        self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        if self.path == "/health": self._json(200 if STATUS["ready"] else 503, STATUS); return
        if self.path == "/status": self._json(200, status_snapshot()); return
        if self.path == "/threads":
            # Where every thread is RIGHT NOW -- to see what a slow or hung
            # tick is waiting on while it is happening. Local callers only:
            # stack frames name files and calls, nothing for the LAN.
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                self.send_error(403); return
            import traceback
            names = {t.ident: t.name for t in threading.enumerate()}
            self._json(200, {names.get(ident, str(ident)): traceback.format_stack(frame)
                             for ident, frame in sys._current_frames().items()})
            return
        if self.path == "/positions":
            try:
                self._json(200, positions_snapshot())
            except Exception as exc:
                print(f"/positions handler failed: {exc}", flush=True)
                self._json(500, {"status": "failed_closed"})
            return
        if self.path == "/scan":
            # tick() already catches its own auto-entry/exit errors per app
            # and per market side (see LiveApp.tick/scan) -- this try/except
            # is only a last-resort backstop so an unexpected exception here
            # returns a clean 500 instead of crashing the connection (which
            # curl surfaces as a 502, as happened 18 Sep 2026 when a Binance
            # rate limit hit mid-batch).
            try:
                self._json(200, {app.tag or "default": app.tick() for app in APPS})
            except Exception as exc:
                print(f"/scan handler failed: {exc}", flush=True)
                self._json(500, {"status": "failed_closed"})
            return
        self.send_error(404)
    def do_POST(self):
        if self.path != "/telegram": self.send_error(404); return
        expected = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
        supplied = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not expected or not hmac.compare_digest(expected, supplied): self.send_error(403); return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 65536: raise ValueError("invalid body size")
            self._json(200, APP.telegram(json.loads(self.rfile.read(length))))
        except Exception: self._json(500, {"status": "failed_closed"})
    def log_message(self, *args): return

class ConsoleLog:
    """Mirror everything the bot prints into runtime/bot.log, one timestamped
    line at a time, so the console can be read (and acted on) without anyone
    watching the PowerShell window -- the user's 23 Sep 2026 request after
    -1021/-2013 errors surfaced only on screen. The file is rotated once past
    max_bytes so it cannot grow without bound."""

    def __init__(self, stream, path, max_bytes=5_000_000, clock=time.time):
        self.stream, self.path, self.max_bytes, self.clock = stream, Path(path), max_bytes, clock
        self.lock = threading.Lock()
        self.pending = ""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and self.path.stat().st_size > max_bytes:
            self.path.replace(self.path.with_suffix(".log.1"))

    def write(self, text):
        # File FIRST, console second (23 Sep 2026): a Windows console in
        # selection ("QuickEdit") mode blocks every write until someone
        # presses a key, and with the console first nothing reached the log
        # either -- not even the watchdog's own message.
        with self.lock:
            self.pending += text
            *lines, self.pending = self.pending.split("\n")
            if lines:
                stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.clock()))
                try:
                    with self.path.open("a", encoding="utf-8") as log:
                        log.writelines(f"{stamp} {line}\n" for line in lines if line.strip())
                except OSError:
                    pass  # logging must never take the bot down
        self.stream.write(text)
        return len(text)

    def flush(self):
        self.stream.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


def main():
    verify_from_environment()
    log_path = Path(os.environ.get("CRYPTO_STORAGE", "runtime")) / "bot.log"
    sys.stdout = ConsoleLog(sys.stdout, log_path)
    sys.stderr = ConsoleLog(sys.stderr, log_path)
    config = json.loads(Path("live_config.json").read_text(encoding="utf-8"))
    # v5 long side (shorter 10/20/40-bar Donchian, uncapped winners via
    # cap_at_target=false) drives live candidates and exits; see ARASTIRMA.md.
    strategy = json.loads(Path("config_v5_long.json").read_text(encoding="utf-8"))
    # v5 short side (Part 2, 18 Sep 2026): same signal mirrored, Binance
    # Futures with leverage tiered 3x/5x by breakout strength. Automatic
    # entry (no Telegram confirmation needed), automatic exits. Deployed
    # directly to live after the signal passed walk-forward + real
    # funding-cost validation -- see ARASTIRMA.md and the user's explicit
    # 18 Sep 2026 instruction.
    short_config = json.loads(Path("short_live_config.json").read_text(encoding="utf-8"))
    # Two parallel systems sharing the same real Binance wallets, split by
    # client-order-id tag (18 Sep 2026, user's explicit instruction): "4"
    # is the original 4H system (rare entries, higher per-trade risk
    # budget), "2" is a new 2H system (more frequent entries, lower
    # per-trade risk budget, its own runtime-state files published by
    # github_worker.write_2h_signal_state so it never touches the 4H
    # candidate feed). Both read the SAME strategy params (config_v5_long/
    # short_live_config's btc_filter, min_breaks, etc.) since the 4H vs 2H
    # A/B test only ever varied the candle interval, not the strategy.
    config_2h = json.loads(Path("live_config_2h.json").read_text(encoding="utf-8"))
    short_config_2h = json.loads(Path("short_live_config_2h.json").read_text(encoding="utf-8"))
    # Candidate detection now runs locally, in this same process's periodic
    # loop (see run_periodic_scans's `detect` param / github_worker.
    # local_tick), instead of solely depending on GitHub Actions' schedule
    # trigger -- live-observed 21 Sep 2026 to actually fire every 2-5 HOURS
    # despite being configured for every 15 minutes, a documented GitHub
    # Actions limitation for high-frequency cron. State files therefore
    # point at this process's own local runtime dir, not the GitHub raw
    # URLs, so entries always act on what this process itself just
    # detected -- never on a stale or hours-delayed remote fetch.
    runtime_dir = Path(os.environ.get("CRYPTO_STORAGE", "runtime")).resolve()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    # Our own orders past Binance's 7-day window and the transfer total
    # (audit A1/A7) are kept here.
    BinanceFuturesMarket.store_dir = runtime_dir
    def local_url(name):
        # Path.as_uri(), not an f-string: on Windows (the PC deployment
        # target) an absolute path uses backslashes and no drive-letter
        # slash ("C:\Users\..."), which urllib fails to parse as a URL --
        # as_uri() produces the correct "file:///C:/Users/..." on Windows
        # and "file:///..." on Linux/macOS alike.
        return (runtime_dir / name).as_uri()
    global APP, APPS
    # Both systems trade the one daily list github_worker.prepare_data ranks
    # by 7-day volume (24 Sep 2026); its order history is what pilot_status
    # reads, instead of re-ranking the whole market every 90 seconds.
    manifest_path = runtime_dir / "manifest.json"
    def manifest_symbols():
        symbols = _read_json(manifest_path, {}).get("symbols")
        return symbols if symbols else universe(strategy)
    APP = LiveApp(config, strategy, os.environ, short_config, strategy, tag="4",
                 state_url=local_url("state-relaxed.json"), state_short_url=local_url("short_state.json"),
                 manifest_path=manifest_path, guard_path=runtime_dir / "daily_guard_4.json",
                 universe_fn=manifest_symbols)
    # The 2H system's own strategy file: exits, trailing and the resting
    # stop's ratchet must use the same 2h-scaled settings its entries were
    # detected with (see github_worker.load_2h_strategy).
    strategy_2h = json.loads(Path("config_v5_long_2h.json").read_text(encoding="utf-8"))
    app_2h = LiveApp(config_2h, strategy_2h, os.environ, short_config_2h, strategy_2h, tag="2",
                     state_url=local_url("state_2h.json"), state_short_url=local_url("short_state_2h.json"),
                     interval=TWO_HOUR, manifest_path=manifest_path,
                     guard_path=runtime_dir / "daily_guard_2.json", universe_fn=manifest_symbols)
    APPS = [APP, app_2h]
    telegram_ready = all(os.environ.get(k) for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"))
    STATUS.update(ready=True, binance_connected=True, telegram_ready=telegram_ready,
                  orders_enabled=execution_enabled(config, os.environ), commit=_running_commit())
    print("Binance connected; orders_enabled=" + str(STATUS["orders_enabled"]), flush=True)
    announce_start(runtime_dir, STATUS["commit"])
    threading.Thread(target=watch_loop, daemon=True).start()
    if telegram_ready:
        # "AL <COIN>" replies for coins outside the automatic list. Polled,
        # since this PC has no public address for a webhook.
        poller = TelegramApprovals(os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"], APPS)
        threading.Thread(target=poller.run, daemon=True).start()
    # Trade ledger (user's request, 24 Sep 2026): every buy and sell with
    # its USDT result, refreshed hourly into ~/kripto/islem-kayitlari.
    ledger_state = {"at": 0.0}
    def refresh_ledger():
        try:
            daily_summary(APP.futures_executor, APP.futures_market, runtime_dir)
        except Exception as exc:
            print(f"Daily summary failed: {exc}", flush=True)
        if time.time() - ledger_state["at"] < 3600:
            return
        ledger_state["at"] = time.time()
        ledger.refresh(APP.futures_executor)
    threading.Thread(target=run_periodic_scans, args=(APPS,),
                     kwargs={"detect": lambda: local_tick(runtime_dir), "housekeeping": refresh_ledger},
                     daemon=True).start()
    # Loopback only on the Windows PC: /scan ticks the live systems and has
    # no password, and nothing outside this machine needs it. Render and
    # the containers (Linux) still listen on every interface.
    host = os.environ.get("HOST") or ("127.0.0.1" if os.name == "nt" else "0.0.0.0")
    ThreadingHTTPServer((host, int(os.environ.get("PORT", "10000"))), Handler).serve_forever()

if __name__ == "__main__": main()
