"""Telegram delivery for hisse alerts. Reuses crypto_v1.telegram's dedup-safe
send primitives (same bot/chat as the crypto pilot, but every message is
prefixed so the two are never confused)."""
import hashlib
from datetime import datetime, timezone
from crypto_v1.telegram import deliver_once

PREFIX = 'HISSE | '


def _bucket(scope, symbol, event, now_s):
    if scope == 'once':
        return str(event.get('dedupe_id', ''))
    dt = datetime.fromtimestamp(now_s, timezone.utc)
    return dt.strftime('%Y-%m-%d') if scope == 'day' else dt.strftime('%Y-W%W')


def dedupe_key(symbol, event, now_s):
    bucket = _bucket(event['dedupe_scope'], symbol, event, now_s)
    return hashlib.sha256(f"{symbol}:{event['kind']}:{bucket}".encode()).hexdigest()


def deliver_scan_results(results, now_s, database):
    """results: hisse.scan.scan_all's first return value. Returns how many
    messages were actually sent (vs. skipped as already-delivered)."""
    sent = 0
    for symbol, events in results.items():
        for event in events:
            key = dedupe_key(symbol, event, now_s)
            if deliver_once(key, PREFIX + event['message'], database):
                sent += 1
    return sent


def notify_errors(errors, database, run_id):
    """A single summary message for the run's failures, not one per symbol --
    avoids spamming Telegram if a data source hiccups for several tickers at
    once. Sent at most once per run_id."""
    if not errors:
        return False
    lines = [f"{s}: {msg}" for s, msg in sorted(errors.items())][:10]
    more = f"\n(+{len(errors) - 10} more)" if len(errors) > 10 else ''
    text = PREFIX + 'Tarama hatalari:\n' + '\n'.join(lines) + more
    key = hashlib.sha256(f'errors:{run_id}'.encode()).hexdigest()
    return deliver_once(key, text, database)
