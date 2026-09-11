import json
import math
import time
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

INTERVAL = 900000
BASE = 'https://data-api.binance.vision'


def get(path, params=None):
    if path not in ('time', 'exchangeInfo', 'ticker/24hr', 'klines'):
        raise ValueError('Only public market-data endpoints allowed')
    url = BASE + '/api/v3/' + path + '?' + urllib.parse.urlencode(params or {})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 418:
                raise RuntimeError('Binance temporary IP ban; stop requests') from exc
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 4:
                raise
            time.sleep(max(float(exc.headers.get('Retry-After', 0)), 2 ** attempt))
        except (urllib.error.URLError, TimeoutError):
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)


def universe(config):
    info = get('exchangeInfo')
    eligible = {s['symbol'] for s in info['symbols']
                if s['quoteAsset'] == 'USDT' and s['status'] == 'TRADING'
                and s.get('isSpotTradingAllowed', False)
                and s['baseAsset'] not in config['excluded_bases']}
    ranked = sorted((t for t in get('ticker/24hr') if t['symbol'] in eligible),
                    key=lambda t: (-float(t['quoteVolume']), t['symbol']))
    return [t['symbol'] for t in ranked[:config['top_n']]]


def validate(rows):
    previous = None
    for r in rows:
        if any(not math.isfinite(r[k]) for k in ('o', 'h', 'l', 'c', 'v')):
            raise ValueError('Non-finite OHLCV value')
        if r['t'] % INTERVAL or (previous is not None and r['t'] != previous + INTERVAL):
            raise ValueError('Duplicate, unordered or missing 15m candle')
        if not (0 < r['l'] <= min(r['o'], r['c']) <= max(r['o'], r['c']) <= r['h']) or r['v'] < 0:
            raise ValueError('Invalid OHLCV candle')
        previous = r['t']
    return rows


def candles(symbol, start, end):
    result = []
    cursor = start
    while cursor < end:
        batch = get('klines', {'symbol': symbol, 'interval': '15m',
                             'startTime': cursor, 'endTime': end - 1, 'limit': 1000})
        if not batch:
            break
        for b in batch:
            if int(b[6]) < end:
                result.append(dict(t=int(b[0]), o=float(b[1]), h=float(b[2]),
                                   l=float(b[3]), c=float(b[4]), v=float(b[5])))
        next_cursor = int(batch[-1][0]) + INTERVAL
        if next_cursor <= cursor:
            raise ValueError('Non advancing pagination')
        cursor = next_cursor
        time.sleep(0.08)
    return validate(result)


def download(config, directory, days):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    end = get('time')['serverTime'] // INTERVAL * INTERVAL
    start = end - int(days * 96) * INTERVAL
    symbols = universe(config)
    manifest = dict(source=BASE, captured_at=end, start=start, end=end,
                    symbols=symbols, timeframe='15m',
                    universe_bias='Current 24h-volume snapshot: survivorship and selection bias; not historical top 50')
    def fetch_symbol(symbol):
        path = directory / (symbol + '.json')
        cached = validate(json.loads(path.read_text(encoding='utf-8'))) if path.exists() else []
        rows = [r for r in cached if start <= r['t'] < end]
        if rows:
            prefix = candles(symbol, start, rows[0]['t']) if rows[0]['t'] > start else []
            rows = prefix + rows + candles(symbol, rows[-1]['t']+INTERVAL, end)
        else:
            rows = candles(symbol, start, end)
        validate(rows)
        path.write_text(json.dumps(rows), encoding='utf-8')
        print(f'{symbol}: {len(rows)} candles', flush=True)
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(fetch_symbol, sorted(set(symbols + ['BTCUSDT']))))
    (directory / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest


def load(directory):
    directory = Path(directory)
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    data = {s: validate(json.loads((directory / (s + '.json')).read_text(encoding='utf-8')))
            for s in sorted(set(manifest['symbols'] + ['BTCUSDT']))}
    return manifest, data
