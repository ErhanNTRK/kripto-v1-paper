"""Render Frankfurt health probe and authenticated Telegram command webhook."""
import hmac, json, os, re, threading, time
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from .binance_account import verify_from_environment
from .binance_futures import FuturesExecutor
from .binance_trade import OrderRejected, SpotExecutor
from .github_worker import local_tick
from .live_controller import approve_buy
from .live_execution import execution_enabled
from .live_market import BinanceMarket
from .live_monitor import execute_exit, exit_decision
from .live_short_controller import approve_short
from .live_short_market import BinanceFuturesMarket
from .live_short_monitor import execute_short_exit, short_exit_decision
from .live_signal import (RUNTIME_STATE, RUNTIME_STATE_SHORT, fetch_runtime_state,
                          fetch_runtime_state_short, isolate_candidate,
                          isolate_short_candidate, pending_candidates,
                          pending_short_candidates)
from .research_v2 import FOUR_HOUR
from .research_v5 import ShortWindowLongModel, symmetric_features
from .telegram import send_message

# "commit" is Render's own RENDER_GIT_COMMIT for the running build, so a
# /health read (which paper.yml now prints into the Actions log) proves
# whether a merge has actually been deployed -- without Render dashboard
# access. Empty outside Render.
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
    quote = Decimal(str(order.get("cummulativeQuoteQty", "0")))
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

class LiveApp:
    # See auto_enter's docstring: bounds Binance API weight per automatic
    # tick so a burst of simultaneous candidates can never itself trip a
    # rate limit and starve the same tick's exit check.
    ENTRIES_PER_TICK = 1

    def __init__(self, config, strategy_config, environment, short_config=None, short_strategy_config=None,
                 tag="", state_url=RUNTIME_STATE, state_short_url=RUNTIME_STATE_SHORT):
        self.config, self.environment = config, environment
        # `tag` segregates capital/positions between parallel systems
        # sharing one real Binance account (18 Sep 2026: 4H tag="4", 2H
        # tag="2") -- see live_market.summarize_pilot / _belongs_to_tag.
        # `state_url`/`state_short_url` let each tagged system read its own
        # independently-published candidate feed (see github_worker.
        # write_2h_signal_state) instead of always reading the 4H files.
        self.tag = tag
        self.state_url, self.state_short_url = state_url, state_short_url
        self._tick_lock = threading.Lock()
        # Most recent tick's full result (rejection reasons included) -- what
        # /status serves so a rejected entry can be diagnosed without Render
        # log access and without spending any Binance weight.
        self.last_tick = None
        self.executor = SpotExecutor(config, environment)
        self.market = BinanceMarket(config, strategy_config, environment, self.executor, tag=tag)
        self.short_config = short_config
        # `is not None`, not truthiness: an empty-but-present short_config
        # dict must still enable the short subsystem, not silently disable
        # it the way a falsy `if short_config` check would.
        self.futures_executor = FuturesExecutor(short_config, environment) if short_config is not None else None
        self.futures_market = (BinanceFuturesMarket(short_config, short_strategy_config, environment,
                                                     self.futures_executor, tag=tag)
                               if short_config is not None else None)

    def _approve_long(self, update_id, saved, tag=""):
        result = approve_buy(update_id, "AL", int(time.time() * 1000),
                             saved, self.config, self.environment,
                             self.market, self.executor)
        label = f"[{tag}] " if tag else ""
        if result["status"] == "preview":
            plan = result["plan"]
            send_message(f"{label}AL onayi dogrulandi: {plan['symbol']} | Planlanan risk {plan['planned_loss_usdt']} USDT. Gercek emir kilidi kapali.")
        elif result["status"] == "bought_and_protected":
            send_message(f"{label}ALDIM: {result['plan']['symbol']} | risk {result['plan']['planned_loss_usdt']} USDT")
        elif result["status"] == "bought_then_emergency_sold":
            send_message(f"{label}ACIL GUVENLIK SATISI: {result['plan']['symbol']} koruyucu stop kurulamadi ve alim geri satildi.")
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
        saved_long = fetch_runtime_state(url=self.state_url)
        saved_short = fetch_runtime_state_short(url=self.state_short_url) if self.futures_executor else {"state": {}}
        long_pending = pending_candidates(saved_long, now_ms, self.config)
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
            isolated = isolate_candidate(saved_long, candidate["symbol"])
            update_id = int(f"{self.tag}{candidate['created_at']}") if self.tag else int(candidate["created_at"])
            result = self._attempt(candidate, "long",
                                   lambda: self._approve_long(update_id, isolated, tag=self.tag))
            results.append({"symbol": candidate["symbol"], "side": "long", "result": result})
            if result.get("status") == "failed" and _rate_limited():
                break
            if result.get("status") not in ("rejected", "failed"):
                taken += 1
        taken = 0
        for candidate in short_pending:
            if taken >= self.ENTRIES_PER_TICK:
                break
            isolated = isolate_short_candidate(saved_short, candidate["symbol"])
            update_id = int(f"{self.tag}{candidate['created_at']}") if self.tag else int(candidate["created_at"])
            result = self._attempt(candidate, "short",
                                   lambda: self._approve_short(update_id, isolated, tag=self.tag))
            results.append({"symbol": candidate["symbol"], "side": "short", "result": result})
            if result.get("status") == "failed" and _rate_limited():
                break
            if result.get("status") not in ("rejected", "failed"):
                taken += 1
        return {"status": "auto_entry", "results": results}

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
        long_pending = pending_candidates(saved_long, now_ms, self.config)
        short_pending = (pending_short_candidates(saved_short, now_ms, self.short_config)
                        if self.futures_executor else [])
        if not long_pending and not short_pending:
            send_message("AL yapilmadi: su an bekleyen bir sinyal yok.")
            return {"status": "rejected", "reason": "no_pending_signal"}
        base_id = parsed["update_id"] * 1000
        results = []
        for i, candidate in enumerate(long_pending):
            isolated = isolate_candidate(saved_long, candidate["symbol"])
            results.append({"symbol": candidate["symbol"], "side": "long",
                            "result": self._approve_long(base_id + i, isolated, tag=self.tag)})
        for i, candidate in enumerate(short_pending, start=len(long_pending)):
            isolated = isolate_short_candidate(saved_short, candidate["symbol"])
            results.append({"symbol": candidate["symbol"], "side": "short",
                            "result": self._approve_short(base_id + i, isolated, tag=self.tag)})
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
        try:
            for position in self.market.live_positions():
                feature, btc, high = self.market.analysis(position, ShortWindowLongModel.features, FOUR_HOUR)
                reason = exit_decision(position, feature, btc, high,
                                       {**self.config, **{"trailing_atr": self.market.strategy_config["trailing_atr"]}},
                                       sell_fn=ShortWindowLongModel.sell)
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
                for position in self.futures_market.live_positions():
                    feature, btc, low = self.futures_market.analysis(position, symmetric_features, FOUR_HOUR)
                    reason = short_exit_decision(position, feature, btc, low, self.short_config)
                    if not reason: continue
                    if not execution_enabled(self.short_config, self.environment):
                        results.append({"status": "preview_exit", "symbol": position["symbol"], "reason": reason})
                        continue
                    result = execute_short_exit(position, reason, self.futures_executor)
                    pnl = _fill_pnl(position["entry"], result.get("order"), "short")
                    send_message(f"SATTIM (SHORT KAPANDI): {position['symbol']}{_pnl_suffix(pnl)} | Neden: {reason}")
                    results.append(result)
            except Exception as exc:
                print(f"Futures exit scan failed: {exc}", flush=True)
                results.append({"status": "scan_failed", "side": "futures", "error": str(exc)})
                _note_if_rate_limited(exc)
        return {"status": "scanned", "results": results}

    def tick(self):
        """One full cycle: try to auto-enter any pending signal, then scan
        open positions for exits. Entries are attempted first so a signal
        that appears and immediately qualifies for exit conditions (rare,
        but possible with very_loose) is still opened and protected before
        anything else runs against it.

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
            result = self._tick()
        except Exception as exc:
            self.last_tick = {"at": time.time(), "result": {"status": "failed", "error": str(exc)}}
            raise
        finally:
            self._tick_lock.release()
        self.last_tick = {"at": time.time(), "result": result}
        return result

    def _tick(self):
        if _rate_limited():
            return {"status": "cooling_down", "entries": {"status": "skipped"}, "exits": {"status": "skipped"}}
        hits_before = _consecutive_rate_limit_hits
        entry_result = {"status": "auto_entry", "results": []}
        try:
            entry_result = self.auto_enter()
        except Exception as exc:
            print(f"Auto-entry failed: {exc}", flush=True)
            _note_if_rate_limited(exc)
        exits = self.scan()
        # A tick that ran to completion without a FRESH -1003 (the counter
        # is unchanged from before this tick) means Binance is responding
        # normally again -- reset the escalation so the next real ban
        # starts probing at the short 5-minute cooldown again, not wherever
        # a previous, unrelated ban left off.
        if _consecutive_rate_limit_hits == hits_before:
            _note_rate_limit_cleared()
        return {"entries": entry_result, "exits": exits}

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
    }


def run_periodic_scans(apps, interval_seconds=300, sleep=time.sleep, max_iterations=None, detect=None):
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
    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        if detect is not None:
            try:
                detect()
            except Exception as exc:
                print(f"Local candidate detection failed: {exc}", flush=True)
        for app in apps:
            try:
                app.tick()
            except Exception as exc:
                print(f"Periodic tick failed: {exc}", flush=True)
        iterations += 1
        sleep(interval_seconds)


class Handler(BaseHTTPRequestHandler):
    def _json(self, status, value):
        body = json.dumps(value, default=str).encode(); self.send_response(status)
        self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        if self.path == "/health": self._json(200 if STATUS["ready"] else 503, STATUS); return
        if self.path == "/status": self._json(200, status_snapshot()); return
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

def main():
    verify_from_environment()
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
    def local_url(name):
        # Path.as_uri(), not an f-string: on Windows (the PC deployment
        # target) an absolute path uses backslashes and no drive-letter
        # slash ("C:\Users\..."), which urllib fails to parse as a URL --
        # as_uri() produces the correct "file:///C:/Users/..." on Windows
        # and "file:///..." on Linux/macOS alike.
        return (runtime_dir / name).as_uri()
    global APP, APPS
    APP = LiveApp(config, strategy, os.environ, short_config, strategy, tag="4",
                 state_url=local_url("state-relaxed.json"), state_short_url=local_url("short_state.json"))
    app_2h = LiveApp(config_2h, strategy, os.environ, short_config_2h, strategy, tag="2",
                     state_url=local_url("state_2h.json"), state_short_url=local_url("short_state_2h.json"))
    APPS = [APP, app_2h]
    telegram_ready = all(os.environ.get(k) for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_WEBHOOK_SECRET"))
    STATUS.update(ready=True, binance_connected=True, telegram_ready=telegram_ready,
                  orders_enabled=execution_enabled(config, os.environ))
    print("Binance connected; orders_enabled=" + str(STATUS["orders_enabled"]), flush=True)
    threading.Thread(target=run_periodic_scans, args=(APPS,),
                     kwargs={"detect": lambda: local_tick(runtime_dir)}, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "10000"))), Handler).serve_forever()

if __name__ == "__main__": main()
