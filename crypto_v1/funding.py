"""Public Binance USD-M Futures funding-rate history, via the bulk-data CDN
(data.binance.vision) -- the same trusted, non-geo-restricted endpoint
family this project already relies on for klines (data-api.binance.vision).
The regular trading REST API (fapi.binance.com) returns HTTP 451 from
GitHub Actions runners (confirmed by a live run), so it cannot be used
here; the CDN's monthly archives are the only public source that works
from CI."""
import csv
import io
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone

BASE = 'https://data.binance.vision/data/futures/um/monthly/fundingRate'


def _month_url(symbol, year, month):
    return f'{BASE}/{symbol}/{symbol}-fundingRate-{year:04d}-{month:02d}.zip'


def _months_between(start_ms, end_ms):
    start = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
    end = datetime.fromtimestamp((end_ms - 1) / 1000, tz=timezone.utc)
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m = m + 1 if m < 12 else 1
        y = y if m != 1 else y + 1


def _fetch_month(symbol, year, month, opener):
    url = _month_url(symbol, year, month)
    try:
        with opener(url, timeout=30) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return []
        raise
    events = []
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        with archive.open(archive.namelist()[0]) as fh:
            for row in csv.DictReader(io.TextIOWrapper(fh, encoding='utf-8')):
                events.append(dict(t=int(float(row['calc_time'])), rate=float(row['last_funding_rate'])))
    return events


def funding_rates(symbol, start, end, opener=urllib.request.urlopen, sleep=time.sleep):
    """Historical funding events for symbol's USD-M perpetual, covering
    [start, end) in ms, from Binance's public monthly data archives.
    Returns [{t, rate}] sorted ascending. A month not yet published
    (Binance publishes each month's archive a few days after it closes,
    so the most recent weeks are typically missing) is silently skipped --
    callers get a real but slightly shorter tail, not an error."""
    result = []
    months = list(_months_between(start, end))
    for i, (year, month) in enumerate(months):
        result.extend(e for e in _fetch_month(symbol, year, month, opener) if start <= e['t'] < end)
        if i < len(months) - 1:
            sleep(0.1)
    result.sort(key=lambda e: e['t'])
    return result
