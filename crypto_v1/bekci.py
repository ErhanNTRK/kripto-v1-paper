"""Watchdog outside the bot (audit C2, second layer), run by Windows Task
Scheduler every 5 minutes. The bot's own Telegram alerts cannot report the
bot itself being gone -- a closed launcher window, a crash the launcher did
not recover from, the whole process hung. This checks it from outside and
tells Telegram once it has been down DOWN_ALERT_SECONDS, then every
REMIND_SECONDS, and once more when it is back.

Standard library only and no package imports, so the task can run it as a
plain script. It never starts, stops or trades anything; the protective
stops rest on Binance either way. It cannot help while the PC itself is
off -- nothing on the PC can."""
import json
import os
import time
import urllib.request
from pathlib import Path

HOME = Path(os.environ.get("USERPROFILE") or Path.home()) / "kripto"
BOT = "http://127.0.0.1:10000"
DOWN_ALERT_SECONDS = 600
# User's request, 25 Sep 2026: a weekend outage must not ping every hour.
REMIND_SECONDS = 6 * 3600
# The bot's own loop watchdog restarts it after 15 minutes without progress.
STALE_TICK_SECONDS = 20 * 60


def _fetch(url):
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.load(response)


def bot_problem(fetch, now):
    """None when the bot answers and its loop ticked recently; otherwise a
    short Turkish reason."""
    try:
        health = fetch(BOT + "/health")
    except Exception:
        return "cevap vermiyor"
    if not health.get("ready"):
        return "hazir degil"
    try:
        status = fetch(BOT + "/status")
    except Exception:
        return "durumunu bildirmiyor"
    ticks = [app["at"] for app in (status.get("apps") or {}).values() if app and app.get("at")]
    if ticks and now - max(ticks) > STALE_TICK_SECONDS:
        return f"{int((now - max(ticks)) // 60)} dakikadir islem turu yapmiyor"
    return None  # no tick yet means it has only just started


def check(state, fetch, send, now):
    """One run. Returns the state to keep until the next one."""
    problem = bot_problem(fetch, now)
    if problem is None:
        if state.get("alerted"):
            try:
                send("BEKCI: Bulutlarin Efendisi yeniden calisiyor.")
            except Exception:
                return state  # say it next run
        return {}
    down = state["down_since"] if state.get("down_since") is not None else now
    state = {"down_since": down, "alerted": state.get("alerted", False), "reminded": state.get("reminded", 0)}
    if now - down < DOWN_ALERT_SECONDS:
        return state
    if state["alerted"] and now - state["reminded"] < REMIND_SECONDS:
        return state
    try:
        send(f"BEKCI: Bulutlarin Efendisi {int((now - down) // 60)} dakikadir {problem}. Stoplar Binance'te "
             "duruyor, ama iz suren cikislar ve yeni girisler calismiyor. Bot penceresine bakin; kapaliysa "
             "masaustundeki Kripto Bot.bat ile baslatin.")
    except Exception:
        return state  # try again next run
    return dict(state, alerted=True, reminded=now)


def _telegram(text):
    secrets = HOME / "secrets"
    token = (secrets / "TELEGRAM_BOT_TOKEN").read_text(encoding="utf-8").strip()
    chat = (secrets / "TELEGRAM_CHAT_ID").read_text(encoding="utf-8").strip()
    body = json.dumps({"chat_id": chat, "text": text}).encode("utf-8")
    request = urllib.request.Request("https://api.telegram.org/bot" + token + "/sendMessage", data=body,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        if not json.load(response).get("ok"):
            raise RuntimeError("Telegram rejected the message")


def main():
    path = HOME / "bekci.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    state = check(state, _fetch, _telegram, time.time())
    path.write_text(json.dumps(state), encoding="utf-8")


if __name__ == "__main__":
    main()
