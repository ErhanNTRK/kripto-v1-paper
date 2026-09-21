"""Scheduled paper runner for GitHub Actions; never submits exchange orders."""
import json
import os
import shutil
from pathlib import Path

from .backtest import report
from .data import candles, get, load, universe, validate
from .paper_trading import tick
from .research_v2 import FOUR_HOUR, TWO_HOUR
from .research_v5 import ShortWindowLongModel
from .risk import validate_config
from .short_signal import detect_long_candidates, detect_short_candidates
from .telegram import deliver_fresh_events, deliver_paper_events, deliver_once, deliver_short_events


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
    if manifest is None or manifest.get("timeframe") != "4h":
        # No cache yet, or a stale pre-V2 cache (15m, top-50 V1 universe) left
        # over from before the V1->V2 switch -- must not be silently reused.
        symbols = universe(config)
        manifest = {"symbols": symbols, "timeframe": "4h", "source": "Binance public market data"}
        write_json(manifest_path, manifest)
    symbols = manifest["symbols"]
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
    for symbol in sorted(set(symbols + ["BTCUSDT"])):
        rows = validate(candles(symbol, start, now, FOUR_HOUR), FOUR_HOUR)
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
    pending_shorts = {c["symbol"]: dict(stop=c["stop"], leverage=c["leverage"])
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
    live_controller.py works for the 2H system completely unmodified."""
    symbols = universe(strategy_config)
    long_symbols = [s for s in symbols if s != "BTCUSDT"]
    # Same 45-day floor as prepare_data's 4H fetch (see its comment on the
    # BTC EMA200 floor) -- at 2H bars this is a much wider margin (~540
    # bars vs the ~200 needed), which is fine, just extra cache-warm data.
    start = now_2h - 45 * 24 * 60 * 60 * 1000
    data = {}
    for symbol in sorted(set(symbols + ["BTCUSDT"])):
        data[symbol] = validate(candles(symbol, start, now_2h, TWO_HOUR), TWO_HOUR)
    long_candidates = detect_long_candidates(data, long_symbols, strategy_config)
    long_events = [dict(type="AL_ADAYI", time=now_2h, symbol=c["symbol"], close=c["close"])
                   for c in long_candidates]
    pending_buys = {c["symbol"]: dict(stop=c["stop"]) for c in long_candidates}
    _write_candidate_state(runtime / "state_2h.json", now_2h, long_events, pending_buys, "pending_buys")
    short_candidates = detect_short_candidates(data, long_symbols, strategy_config)
    short_events = [dict(type="SHORT_ADAYI", time=now_2h, symbol=c["symbol"], close=c["close"])
                    for c in short_candidates]
    pending_shorts = {c["symbol"]: dict(stop=c["stop"], leverage=c["leverage"])
                      for c in short_candidates}
    _write_candidate_state(runtime / "short_state_2h.json", now_2h, short_events, pending_shorts, "pending_shorts")
    print(f"2H tarama: {len(long_symbols)} sembol kontrol edildi, "
         f"{len(long_candidates)} AL_ADAYI, {len(short_candidates)} SHORT_ADAYI bulundu.", flush=True)


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
    write_short_state(config, data_dir, runtime_dir, now)
    write_2h_signal_state(config, runtime_dir, now_2h)
    database = runtime_dir / "telegram.sqlite"
    deliver_paper_events(state_path, database)
    deliver_short_events(runtime_dir / "short_state.json", database)
    deliver_fresh_events(runtime_dir / "state_2h.json", database, "AL_ADAYI", "2h_long")
    deliver_fresh_events(runtime_dir / "short_state_2h.json", database, "SHORT_ADAYI", "2h_short")
    shutil.rmtree(data_dir, ignore_errors=True)
    shutil.rmtree(output, ignore_errors=True)


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
    write_short_state(config, data_dir, runtime, now)
    write_2h_signal_state(config, runtime, now_2h)
    database = runtime / "telegram.sqlite"
    deliver_once(
        "connection:" + chat_id,
        "Kripto V1 baglantisi kuruldu. Sanal takip basladi. Gercek emir verilmiyor.",
        database,
    )
    deliver_paper_events(state_path, database)
    deliver_short_events(runtime / "short_state.json", database)
    deliver_fresh_events(runtime / "state_2h.json", database, "AL_ADAYI", "2h_long")
    deliver_fresh_events(runtime / "short_state_2h.json", database, "SHORT_ADAYI", "2h_short")
    # The once-a-day "Sanal portfoy / Gercek emir verilmedi" status heartbeat
    # was dropped per the user's 18 Sep 2026 request -- it read as confusing
    # noise once live trading was actually turned on (real AL/SHORT_ADAYI
    # alerts and real fill/exit confirmations already show the system is
    # alive; format_daily_status is kept, unused, in case a heartbeat is
    # wanted again later).
    shutil.rmtree(data_dir, ignore_errors=True)
    shutil.rmtree(output, ignore_errors=True)
    print("Paper update completed")


if __name__ == "__main__":
    main()
