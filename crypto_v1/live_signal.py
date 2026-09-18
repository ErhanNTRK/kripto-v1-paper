"""Read the latest paper/short candidates as the sole source of a live
AL/SHORT approval."""
import json
import urllib.request

# paper.yml always sets PAPER_STATE_FILE=state-relaxed.json (so the paper
# tracker's own daily-loss/consecutive-loss halt never silently starves the
# live signal feed) -- plain "state.json" is a stale leftover from before
# that env var existed and is never written by the current pipeline. This
# URL previously pointed at "state.json", meaning the live AL confirmation
# flow could never see a real candidate; fixed 18 Sep 2026.
RUNTIME_STATE = "https://raw.githubusercontent.com/ErhanNTRK/kripto-v1-paper/runtime-state/state-relaxed.json"
RUNTIME_STATE_SHORT = "https://raw.githubusercontent.com/ErhanNTRK/kripto-v1-paper/runtime-state/short_state.json"
# 2H system (18 Sep 2026): published by github_worker.write_2h_signal_state,
# same branch/mechanism as the 4H files above, just distinct filenames so
# neither system can ever overwrite the other's state.
RUNTIME_STATE_2H = "https://raw.githubusercontent.com/ErhanNTRK/kripto-v1-paper/runtime-state/state_2h.json"
RUNTIME_STATE_SHORT_2H = "https://raw.githubusercontent.com/ErhanNTRK/kripto-v1-paper/runtime-state/short_state_2h.json"


def _fetch(url, opener):
    request = urllib.request.Request(url, headers={"User-Agent": "kripto-v1"})
    with opener(request, timeout=20) as response:
        return json.load(response)


def fetch_runtime_state(opener=urllib.request.urlopen, url=RUNTIME_STATE):
    return _fetch(url, opener)


def fetch_runtime_state_short(opener=urllib.request.urlopen, url=RUNTIME_STATE_SHORT):
    return _fetch(url, opener)


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


def isolate_candidate(saved, symbol):
    """Build a saved-state view containing only ONE symbol's AL_ADAYI event
    and pending_buys entry. Lets a caller that found several real,
    simultaneously pending candidates process them one at a time through
    the unmodified, already-tested approve_buy -- which requires seeing
    exactly one candidate -- instead of approve_buy ever having to see
    (and reject as ambiguous) more than one at once. Per the user's 18 Sep
    2026 request: take every candidate we have capacity for, not just
    whichever one happens to be alone."""
    state = saved.get("state", {})
    events = [e for e in state.get("events", []) if e.get("type") == "AL_ADAYI" and e.get("symbol") == symbol]
    pending_buys = state.get("pending_buys", {})
    isolated_pending = {symbol: pending_buys[symbol]} if symbol in pending_buys else {}
    return {"state": {**state, "events": events, "pending_buys": isolated_pending}}


def isolate_short_candidate(saved, symbol):
    state = saved.get("state", {})
    events = [e for e in state.get("events", []) if e.get("type") == "SHORT_ADAYI" and e.get("symbol") == symbol]
    pending_shorts = state.get("pending_shorts", {})
    isolated_pending = {symbol: pending_shorts[symbol]} if symbol in pending_shorts else {}
    return {"state": {**state, "events": events, "pending_shorts": isolated_pending}}


def pending_short_candidates(saved, now_ms, config):
    state = saved.get("state", {})
    expiry = config["signal_confirmation_expiry_minutes"] * 60_000
    pending = state.get("pending_shorts", {})
    latest = {}
    for event in state.get("events", []):
        if event.get("type") != "SHORT_ADAYI":
            continue
        age = now_ms - int(event["time"])
        if 0 <= age <= expiry and event.get("symbol") in pending:
            latest[event["symbol"]] = {
                "symbol": event["symbol"], "created_at": int(event["time"]),
                "close": float(event["close"]), "stop": float(pending[event["symbol"]]["stop"]),
                "leverage": int(pending[event["symbol"]]["leverage"]),
            }
    return [latest[s] for s in sorted(latest)]
