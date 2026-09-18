"""Scheduled entry point (GitHub Actions): scan the BIST + US dividend
universe once and deliver any new alerts to Telegram. Informational only --
this module never places or proposes an order.

The chat id is read (read-only) from the crypto pilot's already-resolved
telegram_chat.json on its runtime-state branch, rather than re-resolving it
via Telegram's getUpdates: that bot has an active webhook (Render's
/telegram endpoint) for the crypto pilot, and Telegram rejects getUpdates
entirely while a webhook is registered (409 Conflict) -- this is the same
bot/token, so the conflict applies here too. Delivery state (which alerts
have already been sent) persists on its own 'hisse-state' git branch, kept
separate from the crypto pilot's own runtime-state branch.
"""
import json
import os
import time
import urllib.request
from pathlib import Path
from . import notify, scan, universe

CRYPTO_CHAT_ID_URL = 'https://raw.githubusercontent.com/ErhanNTRK/kripto-v1-paper/runtime-state/telegram_chat.json'


def resolve_chat_id(runtime):
    chat_file = runtime / 'telegram_chat.json'
    if chat_file.exists():
        return json.loads(chat_file.read_text(encoding='utf-8'))['chat_id']
    request = urllib.request.Request(CRYPTO_CHAT_ID_URL, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(request, timeout=20) as response:
        chat_id = json.load(response)['chat_id']
    chat_file.parent.mkdir(parents=True, exist_ok=True)
    chat_file.write_text(json.dumps({'chat_id': chat_id}), encoding='utf-8')
    return chat_id


def main():
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    if not token:
        raise SystemExit('TELEGRAM_BOT_TOKEN secret is required')
    runtime = Path(os.environ.get('HISSE_STORAGE', 'hisse_runtime'))
    runtime.mkdir(parents=True, exist_ok=True)
    os.environ['TELEGRAM_CHAT_ID'] = resolve_chat_id(runtime)

    now_s = int(time.time())
    # US dividend aristocrats scanning was turned off per the user's 18 Sep
    # 2026 request (he wants BIST-only alerts now). universe.
    # sp500_dividend_aristocrats() is left in place, just unused here, in
    # case US scanning is wanted again later.
    symbols = universe.BIST_WATCHLIST
    results, errors = scan.scan_all(symbols, now_s)
    database = runtime / 'telegram.sqlite'
    sent = notify.deliver_scan_results(results, now_s, database)
    notify.notify_errors(errors, database, run_id=str(now_s // 86400))
    print(f'Scanned {len(symbols)} symbols, {sent} messages sent, {len(errors)} errors', flush=True)


if __name__ == '__main__':
    main()
