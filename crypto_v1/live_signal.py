"""Read the latest paper candidate as the sole source of a live AL approval."""
import json
import urllib.request

RUNTIME_STATE = "https://raw.githubusercontent.com/ErhanNTRK/kripto-v1-paper/runtime-state/state.json"


def fetch_runtime_state(opener=urllib.request.urlopen):
    request = urllib.request.Request(RUNTIME_STATE, headers={"User-Agent": "kripto-v1"})
    with opener(request, timeout=20) as response:
        return json.load(response)


def pending_candidates(saved, now_ms, config):
    state = saved.get("state", {})
    expiry = config["signal_confirmation_expiry_minutes"] * 60_000
    pending = state.get("pending_buys", {})
    latest = {}
    for event in state.get("events", []):
        if event.get("type") != "AL_ADAYI":
            continue
        age = now_ms - int(event["time"])
        if 0 <= age <= expiry and event.get("symbol") in pending:
            latest[event["symbol"]] = {
                "symbol": event["symbol"], "created_at": int(event["time"]),
                "close": float(event["close"]), "stop": float(pending[event["symbol"]]["stop"]),
            }
    return [latest[s] for s in sorted(latest)]
