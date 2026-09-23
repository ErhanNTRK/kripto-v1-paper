import json
import math
import os
import time
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

INTERVAL = 900000
# Public market data (klines, tickers, exchangeInfo). api.binance.com sits
# behind a CDN and answered in ~0.4 s from the live PC on 23 Sep 2026, while
# data-api.binance.vision -- the original choice, because US GitHub Actions
# runners are geo-blocked from api.binance.com -- resolved to bare AWS Tokyo
# hosts that took 2-3 s per call and at times never answered: two live ticks
# sat 9 minutes and 6+ minutes on exactly those addresses. The primary is
# tried first; a geo-block (HTTP 451/403) switches this process to the
# fallback for good, so GitHub Actions keeps working unchanged.
MARKET_DATA_BASES = (os.environ.get('BINANCE_MARKET_DATA_BASE') or 'https://api.binance.com',
                     'https://data-api.binance.vision')
BASE = MARKET_DATA_BASES[0]
SLOW_REQUEST_SECONDS = 15


def note_slow(label, started, outcome="ok"):
    """Print any HTTP call slower than SLOW_REQUEST_SECONDS with its target, so
    a slow or stalled loop can be traced to the exact endpoint."""
    elapsed = time.time() - started
    if elapsed >= SLOW_REQUEST_SECONDS:
        print(f"SLOW REQUEST {elapsed:.1f}s {outcome}: {label}", flush=True)
BINANCE_CODE = {900_000: '15m', 3_600_000: '1h', 7_200_000: '2h', 14_400_000: '4h', 21_600_000: '6h', 86_400_000: '1d'}


def get(path, params=None):
    if path not in ('time', 'exchangeInfo', 'ticker/24hr', 'klines'):
        raise ValueError('Only public market-data endpoints allowed')
    global BASE
    query = '/api/v3/' + path + '?' + urllib.parse.urlencode(params or {})
    for attempt in range(5):
        started = time.time()
        try:
            with urllib.request.urlopen(BASE + query, timeout=30) as response:
                result = json.load(response)
            note_slow(BASE + query[:120], started)
            return result
        except urllib.error.HTTPError as exc:
            note_slow(BASE + query[:120], started, f"HTTP {exc.code}")
            if exc.code in (451, 403) and BASE != MARKET_DATA_BASES[-1]:
                BASE = MARKET_DATA_BASES[-1]   # geo-blocked (e.g. GitHub Actions): use the fallback
                continue
            if exc.code == 418:
                raise RuntimeError('Binance temporary IP ban; stop requests') from exc
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 4:
                raise
            time.sleep(max(float(exc.headers.get('Retry-After', 0)), 2 ** attempt))
        except (urllib.error.URLError, TimeoutError) as exc:
            note_slow(BASE + query[:120], started, type(exc).__name__)
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)


FUTURES_BASE = 'https://fapi.binance.com'


def futures_get(path, params=None):
    if path not in ('exchangeInfo', 'premiumIndex'):
        raise ValueError('Only public futures market-data endpoints allowed')
    url = FUTURES_BASE + '/fapi/v1/' + path + '?' + urllib.parse.urlencode(params or {})
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


def futures_tradable_symbols(min_age_days=0, now_ms=None):
    """min_age_days drops contracts listed too recently (23 Sep 2026, user's
    call after NIL/SAGA): a symbol whose whole price history is a few months
    of its first big move has no earlier range to break out OF, so the
    signal fires on what is really just a first-time vertical move, into
    holders who have never had a chance to take profit before. Binance's own
    onboardDate is the source; a contract without one is kept (the field is
    missing, not zero-aged)."""
    info = futures_get('exchangeInfo')
    now_ms = now_ms if now_ms is not None else info.get('serverTime') or int(time.time() * 1000)
    cutoff = now_ms - int(min_age_days) * 86_400_000
    return {s['symbol'] for s in info['symbols']
            if s.get('quoteAsset') == 'USDT' and s.get('contractType') == 'PERPETUAL'
            and s.get('status') == 'TRADING'
            and (not min_age_days or int(s.get('onboardDate') or 0) <= cutoff
                 or not s.get('onboardDate'))}


def universe(config):
    info = get('exchangeInfo')
    eligible = {s['symbol'] for s in info['symbols']
                if s['quoteAsset'] == 'USDT' and s['status'] == 'TRADING'
                and s.get('isSpotTradingAllowed', False)
                and s['baseAsset'] not in config['excluded_bases']}
    # Every live entry (long and short, both the 4H and 2H systems) executes
    # on Binance Futures, never Spot -- leveraged long replaced unleveraged
    # Spot for new entries on 21 Sep 2026. A Spot-only symbol (no USDT-M
    # perpetual contract) passes every signal check, gets detected as a
    # real candidate, and Binance then rejects every single entry attempt
    # with -1121 "Invalid symbol" -- forever, since the same signal keeps
    # re-qualifying each scan. Live-observed 22 Sep 2026: FETUSDT and
    # MARSCOINUSDT (both Spot-only) were the ONLY two candidates the 2H
    # system found all day, so ENTRIES_PER_TICK capacity was spent
    # retrying two symbols that could never fill while any other real
    # opportunity that day went untaken.
    eligible &= futures_tradable_symbols(config.get('min_listing_age_days', 0))
    ranked = sorted((t for t in get('ticker/24hr') if t['symbol'] in eligible),
                    key=lambda t: (-float(t['quoteVolume']), t['symbol']))
    return [t['symbol'] for t in ranked[:config['top_n']]]


def validate(rows, interval=INTERVAL):
    previous = None
    for r in rows:
        if any(not math.isfinite(r[k]) for k in ('o', 'h', 'l', 'c', 'v')):
            raise ValueError('Non-finite OHLCV value')
        if r['t'] % interval or (previous is not None and r['t'] != previous + interval):
            raise ValueError('Duplicate, unordered or missing candle')
        if not (0 < r['l'] <= min(r['o'], r['c']) <= max(r['o'], r['c']) <= r['h']) or r['v'] < 0:
            raise ValueError('Invalid OHLCV candle')
        previous = r['t']
    return rows


def candles(symbol, start, end, interval=INTERVAL):
    code = BINANCE_CODE[interval]
    result = []
    cursor = start
    while cursor < end:
        batch = get('klines', {'symbol': symbol, 'interval': code,
                             'startTime': cursor, 'endTime': end - 1, 'limit': 1000})
        if not batch:
            break
        for b in batch:
            if int(b[6]) < end:
                result.append(dict(t=int(b[0]), o=float(b[1]), h=float(b[2]),
                                   l=float(b[3]), c=float(b[4]), v=float(b[5])))
        next_cursor = int(batch[-1][0]) + interval
        if next_cursor <= cursor:
            raise ValueError('Non advancing pagination')
        cursor = next_cursor
        time.sleep(0.08)
    return validate(result, interval)


def download(config, directory, days, interval=INTERVAL):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    end = get('time')['serverTime'] // interval * interval
    bars_per_day = 86_400_000 // interval
    start = end - int(days * bars_per_day) * interval
    symbols = universe(config)
    manifest = dict(source=BASE, captured_at=end, start=start, end=end,
                    symbols=symbols, timeframe=BINANCE_CODE[interval], interval=interval,
                    universe_bias='Current 24h-volume snapshot: survivorship and selection bias; not historical top 50')
    def fetch_symbol(symbol):
        path = directory / (symbol + '.json')
        cached = validate(json.loads(path.read_text(encoding='utf-8')), interval) if path.exists() else []
        rows = [r for r in cached if start <= r['t'] < end]
        if rows:
            prefix = candles(symbol, start, rows[0]['t'], interval) if rows[0]['t'] > start else []
            rows = prefix + rows + candles(symbol, rows[-1]['t']+interval, end, interval)
        else:
            rows = candles(symbol, start, end, interval)
        validate(rows, interval)
        path.write_text(json.dumps(rows), encoding='utf-8')
        print(f'{symbol}: {len(rows)} candles', flush=True)
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(fetch_symbol, sorted(set(symbols + ['BTCUSDT']))))
    (directory / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest


def load(directory, interval=INTERVAL):
    directory = Path(directory)
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    data = {s: validate(json.loads((directory / (s + '.json')).read_text(encoding='utf-8')), interval)
            for s in sorted(set(manifest['symbols'] + ['BTCUSDT']))}
    return manifest, data
