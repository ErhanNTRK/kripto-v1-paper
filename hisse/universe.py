"""Stock universes to scan: BIST (curated watchlist) and US (S&P 500
Dividend Aristocrats, fetched live from Wikipedia so it stays current
without a manual edit here every time a company is added/removed).
"""
import json
import re
import urllib.request

WIKI_ARISTOCRATS_URL = (
    'https://en.wikipedia.org/w/api.php?action=parse&page=S%26P_500_Dividend_Aristocrats'
    '&prop=wikitext&format=json&section=1'
)

# No free, complete, live-updating BIST 100 constituent list was found: Borsa
# Istanbul's own site returned 404 on the expected page, and Wikipedia's
# 'BIST 100' article only lists its top ~10 positions by weight (and that
# snapshot is dated). This is a manually curated list of major, liquid,
# historically dividend-paying BIST stocks (~Sep 2026) as a practical stand-in.
# Same documented-bias tradeoff crypto_v1/data.py makes for its own
# 'current top-N by 24h volume' universe -- review/refresh every few months.
BIST_WATCHLIST = [
    'THYAO.IS', 'TUPRS.IS', 'BIMAS.IS', 'AKBNK.IS', 'KCHOL.IS', 'SAHOL.IS',
    'TCELL.IS', 'ISCTR.IS', 'SISE.IS', 'EREGL.IS', 'GARAN.IS', 'YKBNK.IS',
    'ASELS.IS', 'PETKM.IS', 'TOASO.IS', 'FROTO.IS', 'ARCLK.IS', 'TTKOM.IS',
    'VAKBN.IS', 'HALKB.IS', 'SASA.IS', 'PGSUS.IS', 'ENKAI.IS', 'MGROS.IS',
    'ULKER.IS', 'AEFES.IS', 'CCOLA.IS', 'TAVHL.IS', 'DOAS.IS', 'ALARK.IS',
    'TSKB.IS', 'GUBRF.IS', 'VESTL.IS', 'ANHYT.IS',
]


def _get(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read().decode('utf-8')


def parse_aristocrats_tickers(wikitext):
    """Wikipedia's Components table uses '| TICKER || [[Company]] || Sector'
    rows under a '! [[Ticker symbol]] !! ...' header (which this deliberately
    does not match, since it starts with '!' not '|')."""
    return sorted(set(re.findall(r'^\|\s*([A-Z][A-Z.]*)\s*\|\|', wikitext, re.MULTILINE)))


def sp500_dividend_aristocrats():
    payload = json.loads(_get(WIKI_ARISTOCRATS_URL))
    return parse_aristocrats_tickers(payload['parse']['wikitext']['*'])


def full_universe():
    return dict(bist=list(BIST_WATCHLIST), us=sp500_dividend_aristocrats())
