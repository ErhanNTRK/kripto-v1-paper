"""Free, unauthenticated public market data (Yahoo Finance) for BIST and US
tickers, unified through one interface. BIST symbols use the '.IS' suffix
(e.g. 'GARAN.IS'); US symbols are used as-is (e.g. 'KO').

Informational only: nothing here places or even proposes an order. The
quote_summary/dividend_snapshot calls need a short-lived session cookie plus
a 'crumb' token (Yahoo's anti-scraping handshake); the chart endpoint needs
neither.
"""
import http.cookiejar
import json
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = 'Mozilla/5.0'
CHART_URL = 'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}'
CRUMB_URL = 'https://query1.finance.yahoo.com/v1/test/getcrumb'
SEED_URL = 'https://fc.yahoo.com'
QUOTE_SUMMARY_URL = 'https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}'

_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
_crumb_cache = {}


def _get(url, params=None):
    if params:
        url = url + '?' + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with _opener.open(request, timeout=20) as response:
        return response.read()


def _get_json(url, params=None):
    return json.loads(_get(url, params))


def crumb_token():
    """One process-lifetime crumb; Yahoo's anti-scraping handshake needs a
    seeded session cookie first, then this token on every quoteSummary call.
    fc.yahoo.com returns HTTP 404 by design (confirmed: it still sends the
    needed Set-Cookie header) -- only the cookie side effect matters, so a
    non-2xx status there is expected and ignored, not an error."""
    if 'value' not in _crumb_cache:
        try:
            _get(SEED_URL)
        except urllib.error.HTTPError:
            pass
        _crumb_cache['value'] = _get(CRUMB_URL).decode('utf-8').strip()
    return _crumb_cache['value']


def chart(symbol, range='1y', interval='1d', dividends=False):
    """Daily OHLCV (+ dividend/split events if requested). No auth needed."""
    params = dict(range=range, interval=interval)
    if dividends:
        params['events'] = 'div'
    data = _get_json(CHART_URL.format(symbol=symbol), params)
    result = data['chart']['result']
    if not result:
        raise ValueError(f'No chart data for {symbol}: {data["chart"].get("error")}')
    return result[0]


def quote_summary(symbol, modules):
    params = dict(modules=','.join(modules), crumb=crumb_token())
    data = _get_json(QUOTE_SUMMARY_URL.format(symbol=symbol), params)
    result = data['quoteSummary']['result']
    if not result:
        raise ValueError(f'No quoteSummary data for {symbol}: {data["quoteSummary"].get("error")}')
    return result[0]


def _raw(container, key):
    value = (container or {}).get(key)
    return value.get('raw') if isinstance(value, dict) else None


def dividend_snapshot(symbol):
    """One parsed view of what the alerts need: current yield, payout ratio,
    the multi-year average yield (to judge whether 'yield high' is a real
    signal or just this year's noise), and the most recent/next ex-dividend
    and payment dates Yahoo has on file."""
    modules = quote_summary(symbol, ['summaryDetail', 'calendarEvents'])
    detail = modules.get('summaryDetail', {})
    calendar = modules.get('calendarEvents', {})
    return dict(
        symbol=symbol,
        dividend_yield=_raw(detail, 'dividendYield'),
        dividend_rate=_raw(detail, 'dividendRate'),
        payout_ratio=_raw(detail, 'payoutRatio'),
        five_year_avg_yield=_raw(detail, 'fiveYearAvgDividendYield'),
        ex_dividend_date=_raw(detail, 'exDividendDate') or _raw(calendar, 'exDividendDate'),
        dividend_payment_date=_raw(calendar, 'dividendDate'),
    )


def dividend_history(symbol, range='5y'):
    """Actually-paid dividends as a plain (timestamp, amount) list, oldest
    first -- for judging dividend growth/consistency, separate from the
    single forward-looking snapshot above."""
    result = chart(symbol, range=range, interval='1mo', dividends=True)
    events = (result.get('events') or {}).get('dividends') or {}
    return sorted(
        (dict(date=int(v['date']), amount=float(v['amount'])) for v in events.values()),
        key=lambda r: r['date'],
    )
