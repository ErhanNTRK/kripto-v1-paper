"""Explicitly configured private Telegram notifications; no exchange orders."""
import hashlib
import json
import os
import sqlite3
import time
import urllib.request
from datetime import datetime, timezone
from contextlib import closing
from pathlib import Path
from zoneinfo import ZoneInfo


def export_outbox(events, path):
    Path(path).write_text(''.join(json.dumps(e, ensure_ascii=False) + '\n' for e in events), encoding='utf-8')


def send_message(message):
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    chat = os.environ.get('TELEGRAM_CHAT_ID', '')
    if not token or not chat.isdecimal() or int(chat) <= 0:
        raise RuntimeError('Telegram token and verified private chat ID are required')
    body = json.dumps({'chat_id': chat, 'text': message[:4096]}).encode('utf-8')
    request = urllib.request.Request('https://api.telegram.org/bot'+token+'/sendMessage',
                                     data=body, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.load(response)
        if not result.get('ok'):
            raise ValueError('Telegram rejected message')
        return result['result']['message_id']
    except Exception:
        # urllib exceptions can contain the token-bearing request URL.
        raise RuntimeError('Telegram delivery unconfirmed; inspect delivery database before retrying') from None


def format_event(event):
    stamp = datetime.fromtimestamp(event['time']/1000, timezone.utc).strftime('%d.%m %H:%M UTC')
    lines = ['KRIPTO V1 | SANAL ISLEM', f"{event['type']} — {event['symbol']}", stamp]
    for key, label in [('price','Sanal fiyat'),('close','Sinyal kapanisi'),('stop','Stop'),('target','Hedef'),('risk','Planlanan risk (USDT)')]:
        if key in event:
            lines.append(f'{label}: {event[key]:.8g}')
    if 'reason' in event:
        lines.append('Cikis nedeni: '+event['reason'])
    lines.append('Mum bazli simulasyon. Gercek emir verilmedi.')
    return '\n'.join(lines)


def format_daily_status(state, now_ms):
    """Short heartbeat so silence is never confused with a stopped scanner."""
    day = datetime.fromtimestamp(now_ms / 1000, ZoneInfo('Europe/Istanbul')).strftime('%d.%m.%Y')
    trades = [trade for trade in state.get('trades', []) if trade.get('exit_t', 0) >= now_ms - 24 * 60 * 60 * 1000]
    equity = state.get('cash', 0) + sum(position.get('qty', 0) * position.get('entry', 0) for position in state.get('positions', {}).values())
    status = 'GUNLUK ZARAR KESICI AKTIF' if state.get('halted') else 'TARAMA DEVAM EDIYOR'
    return '\n'.join([
        'KRIPTO V1 | GUNLUK DURUM',
        day,
        status,
        f"Son 24 saat kapanan sanal islem: {len(trades)}",
        f"Sanal portfoy: {equity:.2f} USDT",
        'Gercek emir verilmedi.',
    ])


def deliver_once(key, message, database):
    """Reserve before network send. Ambiguous sends are never retried automatically."""
    path = Path(database)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS deliveries (id TEXT PRIMARY KEY, status TEXT, message_id INTEGER)')
        try:
            db.execute('INSERT INTO deliveries VALUES (?, ?, NULL)', (key, 'uncertain'))
            db.commit()
        except sqlite3.IntegrityError:
            return False
        message_id = send_message(message)
        db.execute('UPDATE deliveries SET status=?, message_id=? WHERE id=?', ('sent', message_id, key))
    return True


def deliver_paper_events(state_path, database):
    saved = json.loads(Path(state_path).read_text(encoding='utf-8'))
    # Include paper-session identity and recipient so distinct sessions cannot collide.
    identity = str(saved['state']['started_at']) + ':' + os.environ.get('TELEGRAM_CHAT_ID', '')
    cutoff = saved['state']['started_at']
    sent = 0
    for event in saved['state']['events']:
        if event['time'] < cutoff or event['type'] not in ('AL', 'SAT', 'AL_ADAYI'):
            continue
        key = hashlib.sha256((identity+json.dumps(event, sort_keys=True)).encode()).hexdigest()
        if deliver_once(key, format_event(event), database):
            sent += 1
            time.sleep(1.1)
    return sent


def deliver_short_events(short_state_path, database):
    """SHORT_ADAYI candidates (see github_worker.write_short_state) have no
    persistent session/started_at to filter by -- short_state.json is
    overwritten fresh each cron tick with only that tick's candidates, so
    every event in it is new by construction; deliver_once's own key-based
    idempotency is still the guard against a duplicate send."""
    saved = json.loads(Path(short_state_path).read_text(encoding='utf-8'))
    recipient = os.environ.get('TELEGRAM_CHAT_ID', '')
    sent = 0
    for event in saved['state']['events']:
        if event['type'] != 'SHORT_ADAYI':
            continue
        key = hashlib.sha256(('short:'+recipient+':'+json.dumps(event, sort_keys=True)).encode()).hexdigest()
        if deliver_once(key, format_event(event), database):
            sent += 1
            time.sleep(1.1)
    return sent
