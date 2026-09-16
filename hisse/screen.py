"""Pure screening + message-building logic for the dividend-alert project.
No network calls here -- takes already-fetched hisse.data output and returns
plain dicts/strings, so it is fully testable without mocking any request.

Informational only. Nothing here proposes or places an order; the messages
say so explicitly, and the post-ex-dividend note is deliberately honest that
buying right before and selling right after a dividend is not free money --
the price typically drops by roughly the dividend amount on the ex-date
(mechanical, not a loss), and after tax/commission this is usually flat to
slightly negative for a short holder.
"""
from datetime import datetime, timezone

DEFAULT_MIN_YIELD = 0.02
DEFAULT_MAX_YIELD = 0.15
DEFAULT_MAX_PAYOUT_RATIO = 0.80
DEFAULT_HIGH_YIELD_MARGIN = 1.15  # vs. the stock's own 5-year average


def dividend_quality(snapshot, history, min_yield=DEFAULT_MIN_YIELD,
                      max_yield=DEFAULT_MAX_YIELD, max_payout_ratio=DEFAULT_MAX_PAYOUT_RATIO):
    """Conservative sanity screen. Returns (passes, reasons) so a reject is
    explainable rather than a silent drop.
    - Yield must be known and within [min_yield, max_yield]: an absurdly high
      yield is usually a falling price / dividend-cut risk signal, not a
      bargain, so it is excluded rather than flagged as attractive.
    - Payout ratio (if known) must not be excessive: a company paying out
      more than it earns cannot sustain the dividend.
    - At least 2 historical payments, so a one-off special dividend is not
      mistaken for a recurring one.
    """
    reasons = []
    yld = snapshot.get('dividend_yield')
    if yld is None:
        reasons.append('yield unknown')
    elif not (min_yield <= yld <= max_yield):
        reasons.append(f'yield out of range ({yld:.2%})')
    payout = snapshot.get('payout_ratio')
    if payout is not None and payout > max_payout_ratio:
        reasons.append(f'payout ratio too high ({payout:.0%})')
    if len(history) < 2:
        reasons.append('not enough dividend history')
    return (not reasons, reasons)


def yield_is_notably_high(snapshot, margin=DEFAULT_HIGH_YIELD_MARGIN):
    """True if the current yield is at least `margin`x this stock's OWN
    5-year average -- i.e. high for this specific stock, not just high in
    absolute terms (which usually just means a different, riskier company)."""
    yld, avg = snapshot.get('dividend_yield'), snapshot.get('five_year_avg_yield')
    if yld is None or not avg:
        return False
    return yld >= avg * margin


def trend_ok(closes, sma_period=200, momentum_period=20):
    """Simple, conservative timing filter: price above its long moving
    average and higher than `momentum_period` bars ago -- avoids flagging a
    stock that is free-falling into what only looks like a high yield."""
    if len(closes) < sma_period:
        return False
    sma = sum(closes[-sma_period:]) / sma_period
    return closes[-1] > sma and closes[-1] > closes[-momentum_period]


def days_until(now_s, target_s):
    return (target_s - now_s) / 86400


def _fmt_date(epoch_s):
    return datetime.fromtimestamp(epoch_s, timezone.utc).strftime('%d.%m.%Y')


def high_yield_message(symbol, snapshot):
    return '\n'.join([
        'YUKSEK TEMETTU ADAYI', '', f'Parite: {symbol}',
        f"Guncel verim: {snapshot['dividend_yield']:.2%}",
        f"5 yillik ortalama verim: {snapshot['five_year_avg_yield']:.2%}",
        f"Odeme orani: {snapshot['payout_ratio']:.0%}" if snapshot.get('payout_ratio') is not None else 'Odeme orani: bilinmiyor',
        '', 'Bu bir alim tavsiyesi degildir, sadece bilgilendirmedir.',
    ])


def last_buy_date_message(symbol, snapshot, now_s):
    ex_date = snapshot['ex_dividend_date']
    return '\n'.join([
        'TEMETTU: SON ALIM TARIHI YAKLASIYOR', '', f'Parite: {symbol}',
        f'Hak kullanim (ex-dividend) tarihi: {_fmt_date(ex_date)}',
        'Bu temettuyu almak icin en gec bir onceki is gunu kapanisina kadar elinde bulundurman gerekir.',
        '', 'Not: bu tarihten sonra fiyatta temettu tutari kadar dusus GORULMESI NORMALDIR, kayip degildir.',
    ])


def post_ex_dividend_message(symbol, snapshot):
    rate = snapshot.get('dividend_rate')
    rate_text = f'~{rate:.2f}' if rate is not None else 'temettu tutari kadar'
    return '\n'.join([
        'TEMETTU SONRASI BILGILENDIRME', '', f'Parite: {symbol}',
        f'Hak kullanim tarihi gecti. Fiyatta {rate_text} bir dusus normal, mekanik bir ayarlamadir; kayip degildir.',
        '', "Onemli: 'temettu avciligi' (hemen once alip hemen sonra satmak) genelde ekstra",
        'kazanc saglamaz -- fiyat dususu temettuyu asagi yukari dengeler, ustune vergi ve',
        'komisyon de eklenir. Satis karari tamamen sana ait, bu bir tavsiye degildir.',
    ])
