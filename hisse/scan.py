"""Orchestrates one scan: fetch data for a symbol, apply the dividend-quality
+ trend screen, and return the events to report. Each event carries a
dedupe_scope so hisse.notify can avoid re-sending the same thing every run:
- 'week': a standing condition (e.g. yield is notably high) -- at most one
  reminder a week, not one every run.
- 'day': a date is approaching (last-buy-date window) -- fine to repeat once
  a day as the date gets closer, and it stops on its own once the date passes.
- 'once' (with dedupe_id): a one-time event tied to a specific ex-dividend
  date -- sent exactly once for that date, not once per day it stays in the
  post-ex-dividend window.
"""
from . import data, screen

LAST_BUY_WINDOW_DAYS = 5    # start reminding this many days before ex-date
POST_EX_WINDOW_DAYS = 3     # note the price-drop mechanic for this many days after


def _closes_from_chart(chart_result):
    quotes = (chart_result.get('indicators') or {}).get('quote') or [{}]
    return [c for c in (quotes[0].get('close') or []) if c is not None]


def _price_info(chart_result, closes):
    """Already-realized historical facts only -- never a forecast or target."""
    meta = chart_result.get('meta') or {}
    change_1y = (closes[-1] / closes[0] - 1) if len(closes) >= 2 and closes[0] else None
    return dict(
        current=meta.get('regularMarketPrice'),
        currency=meta.get('currency'),
        change_1y_pct=change_1y,
        week52_low=meta.get('fiftyTwoWeekLow'),
        week52_high=meta.get('fiftyTwoWeekHigh'),
    )


def scan_symbol(symbol, now_s):
    """One symbol's full check. Raises on a data/network problem -- the
    caller (scan_all) is responsible for catching that per-symbol so one bad
    ticker does not stop the whole run."""
    snap = data.dividend_snapshot(symbol)
    history = data.dividend_history(symbol)
    chart_result = data.chart(symbol, range='1y', interval='1d')
    closes = _closes_from_chart(chart_result)
    price = _price_info(chart_result, closes)

    events = []
    quality_ok, _ = screen.dividend_quality(snap, history)
    if quality_ok and screen.yield_is_notably_high(snap) and screen.trend_ok(closes):
        events.append(dict(kind='high_yield', dedupe_scope='week',
                            message=screen.high_yield_message(symbol, snap, price)))

    ex_date = snap.get('ex_dividend_date')
    if ex_date is not None:
        days = screen.days_until(now_s, ex_date)
        if 0 <= days <= LAST_BUY_WINDOW_DAYS:
            events.append(dict(kind='last_buy_date', dedupe_scope='day',
                                message=screen.last_buy_date_message(symbol, snap, now_s, price)))
        elif -POST_EX_WINDOW_DAYS <= days < 0:
            events.append(dict(kind='post_ex_dividend', dedupe_scope='once', dedupe_id=ex_date,
                                message=screen.post_ex_dividend_message(symbol, snap, price)))
    return events


def scan_all(symbols, now_s):
    """{symbol: [event, ...]} for symbols with something to report, plus a
    separate {symbol: error_message} for ones that failed to fetch."""
    results, errors = {}, {}
    for symbol in symbols:
        try:
            events = scan_symbol(symbol, now_s)
        except Exception as exc:
            errors[symbol] = str(exc)
            continue
        if events:
            results[symbol] = events
    return results, errors
