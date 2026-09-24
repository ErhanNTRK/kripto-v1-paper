"""Scheduled paper runner for GitHub Actions; never submits exchange orders."""
import json
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .backtest import report
from .data import candles, futures_tradable_symbols, get, load, universe, validate, weekly_universe
from .paper_trading import tick
from .research_v2 import FOUR_HOUR, TWO_HOUR
from .research_v5 import ShortWindowLongModel
from .risk import validate_config
from .short_signal import detect_long_candidates, detect_short_candidates
from .telegram import deliver_once

# A CEILING, not a fixed value: approve_long_leveraged passes it through
# live_execution.safe_leverage, which lowers it whenever the stop is too
# wide for 4x to stay clear of liquidation. Raised from a fixed 2x on 22
# Sep 2026 (user's explicit decision, together with 2H risk_per_trade_usdt
# 2.0 -> 1.5) so a 34.55 USDT 2H slice can hold 3-4 positions at once
# instead of one position tying up ~20-30 USDT of margin.
TWO_HOUR_LONG_LEVERAGE = 4

# Parallel public-kline fetches. Sequential fetching measured ~3s per
# symbol from the PC (22 Sep 2026), so one detection pass over ~50 symbols
# on both 4H and 2H took ~10 minutes -- a third of the 30-minute entry
# window gone before auto_enter ever saw a candidate. klines costs weight
# 2, so 8 in flight is far below Binance's 6000/min IP budget.
FETCH_WORKERS = 8


def load_2h_strategy(relax=True):
    """The 2H system's own strategy file (23 Sep 2026). Until then 2H reused
    config_v5_long.json verbatim, but every setting there is a BAR count
    tuned on 4h bars, so on 2h bars it measured half the calendar time --
    breakouts of barely 1.7 days, a trail half as wide. Over ~3 years of 2h
    data that made the 2H long side lose in 3 of 6 windows; with the bar
    counts doubled (Donchian 20/40/80, first-time-high over 400 bars) and a
    6 ATR trail, the mean per window went from +4.5% to +14.3%."""
    config = validate_config(json.loads(Path("config_v5_long_2h.json").read_text(encoding="utf-8")))
    return dict(config, daily_loss_fraction=0.05, max_consecutive_losses=100000) if relax else config


def btc_history_start(config, now, default_start):
    """BTC needs a longer history than the altcoins whenever the regime
    filter is on: research_v5's regime line is a regime_ma_days average,
    and long_entry fails CLOSED while that line is unknown. With the old
    45-day fetch a 200-day filter would have silently blocked every long."""
    days = config.get("regime_ma_days")
    if not days:
        return default_start
    return min(default_start, now - (int(days) + 20) * 24 * 60 * 60 * 1000)


def fetch_all(symbols, start, end, interval, btc_start=None):
    symbols = sorted(set(symbols))
    starts = {s: (btc_start if s == "BTCUSDT" and btc_start is not None else start) for s in symbols}
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        rows = list(pool.map(lambda s: validate(candles(s, starts[s], end, interval), interval), symbols))
    return dict(zip(symbols, rows))


def private_chat_id(token):
    """Resolve one private chat that already sent /start; fail closed if ambiguous."""
    import urllib.request
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/getUpdates",
        data=b"{}",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.load(response)
    except Exception:
        raise RuntimeError("Telegram chat lookup failed") from None
    chats = {
        str(item["message"]["chat"]["id"])
        for item in payload.get("result", [])
        if item.get("message", {}).get("chat", {}).get("type") == "private"
        and item.get("message", {}).get("text", "").strip().lower() == "/start"
    }
    if len(chats) != 1:
        raise RuntimeError("Exactly one private /start chat is required")
    return chats.pop()


def write_json(path, value):
    """Atomic (write to a sibling temp file, then rename) since local_tick
    now runs in the same live process as LiveApp's own periodic tick, which
    reads these same files from a different point in its loop -- a plain
    write left a window where a concurrent read could see a truncated file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def prepare_data(config, runtime, now):
    data_dir = runtime / "data"
    manifest_path = runtime / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else None
    # Re-ranked once a day (23 Sep 2026). It used to be ranked once and then
    # kept forever to hold the paper state's fingerprint steady, so the 4H
    # side scanned a frozen 22 Sep list: ACEUSDT, in today's top 50, fired a
    # signal the 4H system never saw, while the 2H side (ranked every pass)
    # did. A new list changes the fingerprint and local_tick starts a fresh
    # paper state -- harmless, that state only feeds candidate detection.
    # A manifest without ranked_at predates this and is refreshed too.
    stale = (manifest is None or manifest.get("timeframe") != "4h"
             or "ranked_by" not in manifest
             or now - int(manifest.get("ranked_at", -86_400_000)) >= 86_400_000)
    if stale:
        # Also covers a pre-V2 cache (15m, top-50 V1 universe) left over
        # from before the V1->V2 switch -- must not be silently reused.
        # Ranked by 7-day volume (24 Sep 2026); this list is also the 2H
        # system's list and, in order, what render_web buys without asking.
        try:
            symbols = weekly_universe(config, now)
            ranked_at, ranked_by = now, "7d_volume"
        except Exception as exc:
            # Keep trading yesterday's list (or today's 24h one on a first
            # start) and try the ranking again in about an hour.
            print(f"7-day volume ranking failed, list kept: {exc}", flush=True)
            kept = manifest and manifest.get("timeframe") == "4h" and manifest.get("symbols")
            symbols = manifest["symbols"] if kept else universe(config)
            ranked_at, ranked_by = now - 23 * 3_600_000, "kept_after_failure" if kept else "24h_volume"
        manifest = {"symbols": symbols, "timeframe": "4h", "source": "Binance public market data",
                    "ranked_at": ranked_at, "ranked_by": ranked_by}
        write_json(manifest_path, manifest)
    symbols = manifest["symbols"]
    # Between daily re-ranks the cached list is reused as-is, so a manifest written before
    # universe() started filtering to Futures-tradable symbols (22 Sep
    # 2026) still carried Spot-only symbols that no live entry can fill --
    # the 4H long/short detectors and the paper engine kept flagging them.
    # Same filter here; persisted so the fingerprint settles after one
    # reset. An empty tradable set means the lookup itself failed, not
    # that nothing is tradable -- leave the list alone rather than wipe it.
    tradable = futures_tradable_symbols()
    if tradable:
        filtered = [s for s in symbols if s in tradable]
        if filtered != symbols:
            symbols = filtered
            manifest = dict(manifest, symbols=symbols)
            write_json(manifest_path, manifest)
    # The real floor here is the BTC EMA200 filter inside btc_up_ok/
    # btc_down_ok (indicators.features needs >=200 closes before ema200 is
    # non-None at all) -- NOT v5's own 40-bar Donchian lookback, which is
    # much shorter. 200 bars = ~33.3 days; 45 days gives a real margin.
    # A previous 30-day window (180 bars) silently meant ema200 never
    # populated, so btc_up_ok/btc_down_ok were always False and no entry
    # signal could ever fire -- caught by zero trades/events in the live
    # paper state despite real market moves (18 Sep 2026).
    start = now - 45 * 24 * 60 * 60 * 1000
    data_dir.mkdir(parents=True, exist_ok=True)
    fetched = fetch_all(symbols + ["BTCUSDT"], start, now, FOUR_HOUR,
                        btc_start=btc_history_start(config, now, start))
    for symbol, rows in fetched.items():
        write_json(data_dir / f"{symbol}.json", rows)
    run_manifest = dict(manifest, start=start, end=now, captured_at=now)
    write_json(data_dir / "manifest.json", run_manifest)
    return data_dir


def _write_candidate_state(path, now, events, pending, pending_key):
    """Merge into the previous write instead of blindly overwriting, but
    ONLY when it's still the same candle window (the previous write's own
    "window" marker equals `now`) -- a new window still fully replaces, so
    a candle that has moved on drops its old candidates as before.

    Bug found live 21 Sep 2026, the first day local_tick ran every 5
    minutes instead of GitHub Actions' rare cron: universe() re-ranks the
    top_n symbols by 24h quoteVolume fresh on EVERY call, and in a fast-
    moving market that ranking visibly shifts within minutes. A symbol
    that qualified and got a real AL_ADAYI Telegram message on one pass
    could drop out of the top_n list on the VERY NEXT pass (5 minutes
    later, same candle, well inside signal_confirmation_expiry_minutes) --
    and since write_2h_signal_state/write_short_state rebuilt `events`/
    `pending_buys` from scratch each call, that symbol's entry vanished
    from the published state entirely, so auto_enter never saw it again
    even though the candidate was still fresh by every timing rule.
    Merging by symbol (newest re-detection wins, an old one otherwise
    kept) fixes this without weakening the real freshness check, which
    still lives in live_signal.pending_candidates and is unaffected."""
    previous = None
    if path.exists():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = None
    if previous and previous.get("state", {}).get("window") == now:
        old_state = previous["state"]
        merged_pending = dict(old_state.get(pending_key, {}))
        merged_pending.update(pending)
        by_symbol = {e["symbol"]: e for e in old_state.get("events", [])}
        by_symbol.update({e["symbol"]: e for e in events})
        events = [by_symbol[s] for s in sorted(by_symbol)]
        pending = merged_pending
    write_json(path, dict(state={"events": events, pending_key: pending, "window": now}))


def _print_4h_long_scan(data_dir, state_path, now):
    """Visible progress for the 4H long (AL_ADAYI) side, matching
    write_short_state/write_2h_signal_state's own print lines -- this path
    (the paper-engine-driven AL_ADAYI feed used for real 4H long entries)
    printed nothing at all, silently sandwiched between "Binance connected"
    and the next line. Live-reported 22 Sep 2026 as "sanki sadece short
    ariyor ve sadece 4H icin ariyor gibi" -- it was actually running fine,
    just invisible, right before write_short_state's own line printed."""
    manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    saved_state = json.loads(state_path.read_text(encoding="utf-8"))["state"]
    found = sum(1 for e in saved_state.get("events", [])
               if e.get("type") == "AL_ADAYI" and e.get("time") == now)
    print(f"4H long tarama: {len(manifest['symbols'])} sembol kontrol edildi, {found} AL_ADAYI bulundu.",
         flush=True)


def write_short_state(short_config, data_dir, runtime, now):
    """Detect live short candidates from the same freshly-fetched 4h
    universe the long paper tick just used, and publish them the same way
    AL_ADAYI candidates are published for the long side (see
    live_signal.pending_candidates). This is a separate, lightweight pass
    -- not routed through paper_trading's Engine, which is long-only --
    since the short side goes live directly with no paper-tracking phase,
    per the user's 18 Sep 2026 instruction."""
    manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    _, data = load(data_dir, FOUR_HOUR)
    symbols = [s for s in manifest["symbols"] if s != "BTCUSDT"]
    candidates = detect_short_candidates(data, symbols, short_config)
    events = [dict(type="SHORT_ADAYI", time=now, symbol=c["symbol"], close=c["close"])
              for c in candidates]
    pending_shorts = {c["symbol"]: dict(stop=c["stop"], leverage=c["leverage"],
                                         breaks_down=c["breaks_down"], score=c["volume_ratio"])
                      for c in candidates}
    _write_candidate_state(runtime / "short_state.json", now, events, pending_shorts, "pending_shorts")
    # Visible in the live process's own console (e.g. the PC's PowerShell
    # window) so "why did 0 candidates come out of N symbols" is
    # answerable by eye, not just by reading the strategy code -- added
    # 21 Sep 2026 after the user questioned whether a scan of a visibly
    # rising market finding nothing meant the logic was wrong (it wasn't:
    # this is a fresh-breakout detector, not an "is it up today" filter,
    # and min_breaks/btc_filter were already at their loosest validated
    # setting -- see research_v5.btc_up_ok's docstring).
    print(f"4H short tarama: {len(symbols)} sembol kontrol edildi, {len(candidates)} SHORT_ADAYI bulundu.",
         flush=True)


def write_2h_signal_state(strategy_config, runtime, now_2h):
    """Independent 2H-interval scan for the parallel 2H system (18 Sep
    2026): its own fresh fetch and detect pass, deliberately NOT reusing
    prepare_data/data_dir (4H) so a bug here can never affect the proven,
    already-live 4H detection path above. Publishes AL_ADAYI/SHORT_ADAYI
    state in the exact same shape the 4H files use (see write_short_state
    and paper_trading's own state), so the existing pending_candidates/
    isolate_candidate/approve_buy machinery in live_signal.py and
    live_controller.py works for the 2H system completely unmodified.

    Coin list (24 Sep 2026): the 4H system's daily list (runtime/manifest.json,
    ranked by 7-day volume), as the research tested -- not a fresh 24h-volume
    ranking on every scan, which pulled coins in mid-pump (LSK was stopped out
    twice within an hour; SUPER, TUT, MARSCOIN were never in the tested list).
    Only when that list is missing or over two days old is the market ranked
    here."""
    manifest = {}
    try:
        manifest = json.loads((runtime / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    fresh = now_2h - int(manifest.get("ranked_at", 0) or 0) < 2 * 86_400_000
    symbols = manifest.get("symbols") if fresh and manifest.get("symbols") else universe(strategy_config)
    long_symbols = [s for s in symbols if s != "BTCUSDT"]
    # Same 45-day floor as prepare_data's 4H fetch (see its comment on the
    # BTC EMA200 floor) -- at 2H bars this is a much wider margin (~540
    # bars vs the ~200 needed), which is fine, just extra cache-warm data.
    start = now_2h - 45 * 24 * 60 * 60 * 1000
    data = fetch_all(symbols + ["BTCUSDT"], start, now_2h, TWO_HOUR,
                     btc_start=btc_history_start(strategy_config, now_2h, start))
    long_candidates = detect_long_candidates(data, long_symbols, strategy_config)
    long_events = [dict(type="AL_ADAYI", time=now_2h, symbol=c["symbol"], close=c["close"])
                   for c in long_candidates]
    # TWO_HOUR_LONG_LEVERAGE ceiling, not the strength-tiered 3x/5x: the 2H
    # v5 walk-forward is NO_GO overall (3/5 windows, not 5/5), so 2H is sized
    # for more, smaller positions (see the constant) rather than bigger ones.
    pending_buys = {c["symbol"]: dict(stop=c["stop"], leverage=TWO_HOUR_LONG_LEVERAGE,
                                     breaks_up=c["breaks_up"], score=c["volume_ratio"])
                    for c in long_candidates}
    _write_candidate_state(runtime / "state_2h.json", now_2h, long_events, pending_buys, "pending_buys")
    short_candidates = detect_short_candidates(data, long_symbols, strategy_config)
    short_events = [dict(type="SHORT_ADAYI", time=now_2h, symbol=c["symbol"], close=c["close"])
                    for c in short_candidates]
    pending_shorts = {c["symbol"]: dict(stop=c["stop"], leverage=c["leverage"],
                                         breaks_down=c["breaks_down"], score=c["volume_ratio"])
                      for c in short_candidates}
    _write_candidate_state(runtime / "short_state_2h.json", now_2h, short_events, pending_shorts, "pending_shorts")
    print(f"2H tarama: {len(long_symbols)} sembol kontrol edildi, "
         f"{len(long_candidates)} AL_ADAYI, {len(short_candidates)} SHORT_ADAYI bulundu.", flush=True)


# {runtime dir: {"4h": window, "2h": window}} last fully detected, per
# process. Signals are evaluated on CLOSED candles only, so re-running a
# window that already completed can find nothing new -- it only re-spent
# ~100 kline requests and minutes of wall-clock every loop (22 Sep 2026),
# which kept the loop too slow to retry entries often inside their 30-min
# window. A window is marked only after its pass finishes, so a failed pass
# (network error, rate limit) is simply retried on the next loop.
_DETECTED_WINDOWS = {}


def local_tick(runtime_dir):
    """Runs the same live-candidate-detection pipeline as main() (data fetch,
    paper-engine tick for 4H AL_ADAYI, write_short_state, write_2h_signal_state,
    Telegram delivery), but synchronously and repeatably from within the live
    execution process itself (render_web.run_periodic_scans's own loop, see
    its `detect` parameter) instead of GitHub Actions' schedule trigger.

    Live-observed 21 Sep 2026: paper.yml is configured to run every 15
    minutes but GitHub's free-tier scheduler was actually firing it every
    2-5 HOURS -- a documented GitHub Actions limitation for high-frequency
    cron, not something fixable by asking harder. A candidate detected
    hours after its actual candle close is often already past
    signal_confirmation_expiry_minutes, or still "fresh" by timestamp but
    at a price that has drifted past max_entry_drift_fraction -- either way
    it gets silently rejected, which likely explains at least some of the
    historical "AL_ADAYI geldi, ALDIM gelmedi" reports independent of the
    separate Binance IP-ban issue. run_periodic_scans's own docstring
    already states this exact principle for entries/exits; this extends it
    to detection, which is the piece that was still solely GitHub-Actions-
    gated.

    TELEGRAM_CHAT_ID is read directly from the environment here (already a
    required secret for render_web to send anything at all) rather than
    auto-resolved via private_chat_id -- that resolution mutates
    os.environ globally and would race with the rest of the live process
    reading the same variable."""
    runtime_dir = Path(runtime_dir)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    config = validate_config(json.loads(Path("config_v5_long.json").read_text(encoding="utf-8")))
    # Same override paper.yml applies via PAPER_RELAX_LIMITS=1: this paper
    # engine only decides whether to flag a candidate, never risks real
    # money itself (live_config.json/live_limits.py do that separately at
    # the execution layer), so its own daily-loss/consecutive-loss halt
    # must never be allowed to silently starve the live candidate feed.
    config = dict(config, daily_loss_fraction=0.05, max_consecutive_losses=100000)
    server_time = get("time")["serverTime"]
    now = server_time // FOUR_HOUR * FOUR_HOUR
    now_2h = server_time // TWO_HOUR * TWO_HOUR
    done = _DETECTED_WINDOWS.setdefault(str(runtime_dir.resolve()), {})
    if done.get("4h") != now:
        data_dir = prepare_data(config, runtime_dir, now)
        state_path = runtime_dir / "state-relaxed.json"
        output = runtime_dir / "report"
        try:
            tick(config, data_dir, state_path, output, model=ShortWindowLongModel, interval=FOUR_HOUR)
        except ValueError as error:
            if "new paper state" not in str(error):
                raise
            state_path.unlink(missing_ok=True)
            tick(config, data_dir, state_path, output, model=ShortWindowLongModel, interval=FOUR_HOUR)
        _print_4h_long_scan(data_dir, state_path, now)
        write_short_state(config, data_dir, runtime_dir, now)
        shutil.rmtree(data_dir, ignore_errors=True)
        shutil.rmtree(output, ignore_errors=True)
        done["4h"] = now
    if done.get("2h") != now_2h:
        write_2h_signal_state(load_2h_strategy(), runtime_dir, now_2h)
        done["2h"] = now_2h


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN secret is required")
    runtime = Path(os.environ.get("CRYPTO_STORAGE", "runtime"))
    runtime.mkdir(parents=True, exist_ok=True)
    chat_file = runtime / "telegram_chat.json"
    if chat_file.exists():
        chat_id = json.loads(chat_file.read_text(encoding="utf-8"))["chat_id"]
    else:
        chat_id = private_chat_id(token)
        write_json(chat_file, {"chat_id": chat_id})
    os.environ["TELEGRAM_CHAT_ID"] = chat_id
    # v5 long side (shorter 10/20/40-bar Donchian, uncapped winners via
    # cap_at_target=false) drives the live AL_ADAYI candidates published to
    # runtime-state, per the user's 17 Sep 2026 request; see ARASTIRMA.md.
    # V2 (20/40/80) was dropped from this path after only 2/5 walk-forward
    # windows passed; v5 passed 5/5 (deterministic, manually spot-checked --
    # still a first-time result on one dataset, watch it closely).
    config = validate_config(json.loads(Path("config_v5_long.json").read_text(encoding="utf-8")))
    # Paper-only overrides. Live trading reads live_config.json and is unaffected.
    if os.environ.get("PAPER_RELAX_LIMITS") == "1":
        config = dict(config, daily_loss_fraction=0.05, max_consecutive_losses=100000)
    server_time = get("time")["serverTime"]
    now = server_time // FOUR_HOUR * FOUR_HOUR
    now_2h = server_time // TWO_HOUR * TWO_HOUR
    data_dir = prepare_data(config, runtime, now)
    state_path = runtime / os.environ.get("PAPER_STATE_FILE", "state.json")
    output = runtime / "report"
    try:
        tick(config, data_dir, state_path, output, model=ShortWindowLongModel, interval=FOUR_HOUR)
    except ValueError as error:
        # Expected exactly once per model/config switch (e.g. V1->V2, now
        # V2->v5): an old state is intentionally incompatible and must not
        # be silently reused. Starting a fresh state here is the
        # documented, sanctioned response, not a workaround for the check.
        if "new paper state" not in str(error):
            raise
        state_path.unlink(missing_ok=True)
        tick(config, data_dir, state_path, output, model=ShortWindowLongModel, interval=FOUR_HOUR)
    _print_4h_long_scan(data_dir, state_path, now)
    write_short_state(config, data_dir, runtime, now)
    write_2h_signal_state(load_2h_strategy(), runtime, now_2h)
    database = runtime / "telegram.sqlite"
    deliver_once(
        "connection:" + chat_id,
        "Kripto V1 baglantisi kuruldu. Sanal takip basladi. Gercek emir verilmiyor.",
        database,
    )
    # AL/SAT paper-engine "fill" messages dropped too (21 Sep 2026, user
    # report): this whole state is a closed-candle SHADOW simulation
    # (paper_trading.tick, real_order_enabled=False, every event stamped
    # simulated=True) that never places a real order -- format_event's
    # generic "KRIPTO V1 | SINYAL / AL - SYMBOL" rendering gave no hint of
    # that, so a simulated buy looked identical to a real one and the user
    # reasonably expected a real purchase to follow. The shadow engine
    # itself (tick() above) still runs for ongoing model validation; only
    # its Telegram delivery is removed.
    # The once-a-day "Sanal portfoy / Gercek emir verilmedi" status heartbeat
    # was dropped per the user's 18 Sep 2026 request -- it read as confusing
    # noise once live trading was actually turned on (real fill/exit
    # confirmations already show the system is alive; format_daily_status
    # is kept, unused, in case a heartbeat is wanted again later).
    shutil.rmtree(data_dir, ignore_errors=True)
    shutil.rmtree(output, ignore_errors=True)
    print("Paper update completed")


if __name__ == "__main__":
    main()
