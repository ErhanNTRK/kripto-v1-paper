"""Scheduled paper runner for GitHub Actions; never submits exchange orders."""
import json
import os
import shutil
from pathlib import Path

from .backtest import report
from .data import INTERVAL, candles, get, universe, validate
from .paper_trading import tick
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
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        symbols = manifest["symbols"]
    else:
        symbols = universe(config)
        manifest = {"symbols": symbols, "timeframe": "15m", "source": "Binance public market data"}
        write_json(manifest_path, manifest)
    start = now - 14 * 24 * 60 * 60 * 1000
    data_dir.mkdir(parents=True, exist_ok=True)
    for symbol in sorted(set(symbols + ["BTCUSDT"])):
        rows = validate(candles(symbol, start, now))
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
    config = validate_config(json.loads(Path("config.json").read_text(encoding="utf-8")))
    # Paper-only overrides. Live trading reads live_config.json and is unaffected.
    if os.environ.get("PAPER_RELAX_LIMITS") == "1":
        config = dict(config, daily_loss_fraction=0.05, max_consecutive_losses=100000)
    now = get("time")["serverTime"] // INTERVAL * INTERVAL
    data_dir = prepare_data(config, runtime, now)
    state_path = runtime / os.environ.get("PAPER_STATE_FILE", "state.json")
    output = runtime / "report"
    tick(config, data_dir, state_path, output)
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
