"""Scheduled paper runner for GitHub Actions; never submits exchange orders."""
import json
import os
import shutil
from pathlib import Path

from .backtest import report
from .data import candles, get, universe, validate
from .paper_trading import tick
from .research_v2 import FOUR_HOUR
from .research_v5 import ShortWindowLongModel
from .risk import validate_config
from .telegram import deliver_paper_events, deliver_once, format_daily_status


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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


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
    # >40 four-hour bars (v5's longest lookback) with a comfortable margin,
    # since this cache is rebuilt from scratch on every 15-minute cron run.
    start = now - 30 * 24 * 60 * 60 * 1000
    data_dir.mkdir(parents=True, exist_ok=True)
    for symbol in sorted(set(symbols + ["BTCUSDT"])):
        rows = validate(candles(symbol, start, now, FOUR_HOUR), FOUR_HOUR)
        write_json(data_dir / f"{symbol}.json", rows)
    run_manifest = dict(manifest, start=start, end=now, captured_at=now)
    write_json(data_dir / "manifest.json", run_manifest)
    return data_dir


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
    now = get("time")["serverTime"] // FOUR_HOUR * FOUR_HOUR
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
    database = runtime / "telegram.sqlite"
    deliver_once(
        "connection:" + chat_id,
        "Kripto V1 baglantisi kuruldu. Sanal takip basladi. Gercek emir verilmiyor.",
        database,
    )
    deliver_paper_events(state_path, database)
    saved = json.loads(state_path.read_text(encoding="utf-8"))["state"]
    from datetime import datetime
    from zoneinfo import ZoneInfo
    status_day = datetime.fromtimestamp(now / 1000, ZoneInfo("Europe/Istanbul")).strftime("%Y-%m-%d")
    deliver_once(
        "daily-status:" + chat_id + ":" + str(saved["started_at"]) + ":" + status_day,
        format_daily_status(saved, now),
        database,
    )
    shutil.rmtree(data_dir, ignore_errors=True)
    shutil.rmtree(output, ignore_errors=True)
    print("Paper update completed")


if __name__ == "__main__":
    main()
