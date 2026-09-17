"""Public Binance USD-M Futures funding-rate history (read-only, no auth,
no order endpoints) -- used to model the real cost/benefit of holding a
leveraged perpetual short, which research_v5's unleveraged spot-style
simulator does not include (see its module docstring)."""
import json
import time
import urllib.request
import urllib.parse
import urllib.error

FAPI_BASE = 'https://fapi.binance.com'


def get(path, params=None):
    if path != 'fundingRate':
        raise ValueError('Only the public funding-rate endpoint is allowed')
    url = FAPI_BASE + '/fapi/v1/' + path + '?' + urllib.parse.urlencode(params or {})
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


def funding_rates(symbol, start, end):
    """Historical funding events (every 8h) for symbol's USD-M perpetual,
    covering [start, end) in ms. Returns [{t, rate}] sorted ascending; rate
    is the fraction paid by longs to shorts each event (negative means
    shorts pay longs instead)."""
    result = []
    cursor = start
    while cursor < end:
        batch = get('fundingRate', {'symbol': symbol, 'startTime': cursor,
                                     'endTime': end - 1, 'limit': 1000})
        if not batch:
            break
        for b in batch:
            result.append(dict(t=int(b['fundingTime']), rate=float(b['fundingRate'])))
        next_cursor = int(batch[-1]['fundingTime']) + 1
        if next_cursor <= cursor:
            raise ValueError('Non advancing pagination')
        cursor = next_cursor
        time.sleep(0.08)
    return result
