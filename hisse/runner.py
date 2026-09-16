"""Scheduled entry point (GitHub Actions): scan the BIST + US dividend
universe once and deliver any new alerts to Telegram. Informational only --
this module never places or proposes an order.

Reuses crypto_v1.github_worker's private-chat resolution/caching so no new
Telegram secret is needed beyond the existing TELEGRAM_BOT_TOKEN; delivery
state (which alerts have already been sent) persists on its own 'hisse-state'
git branch, kept separate from the crypto pilot's own runtime-state branch.
"""
import json
import os
import time
from pathlib import Path
from crypto_v1.github_worker import private_chat_id, write_json
from . import notify, scan, universe


def main():
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    if not token:
        raise SystemExit('TELEGRAM_BOT_TOKEN secret is required')
    runtime = Path(os.environ.get('HISSE_STORAGE', 'hisse_runtime'))
    runtime.mkdir(parents=True, exist_ok=True)

    chat_file = runtime / 'telegram_chat.json'
    if chat_file.exists():
        chat_id = json.loads(chat_file.read_text(encoding='utf-8'))['chat_id']
    else:
        chat_id = private_chat_id(token)
        write_json(chat_file, {'chat_id': chat_id})
    os.environ['TELEGRAM_CHAT_ID'] = chat_id

    now_s = int(time.time())
    symbols = universe.BIST_WATCHLIST + universe.sp500_dividend_aristocrats()
    results, errors = scan.scan_all(symbols, now_s)
    database = runtime / 'telegram.sqlite'
    sent = notify.deliver_scan_results(results, now_s, database)
    notify.notify_errors(errors, database, run_id=str(now_s // 86400))
    print(f'Scanned {len(symbols)} symbols, {sent} messages sent, {len(errors)} errors', flush=True)


if __name__ == '__main__':
    main()
