"""Ak Yatirim's public Model Portfoy (BIST stock picks from a licensed
brokerage's own research team), used ONLY to cross-reference our own
dividend/trend screen -- never as a separate universe or a standalone
alert. Any target price relayed to the user is explicitly attributed as
Ak Yatirim's own published figure, not our forecast (we do not generate
price targets ourselves -- see hisse/screen.py's docstring).
"""
import json
import urllib.request

MODEL_PORTFOY_URL = (
    'https://www.akyatirim.com.tr/umbraco/surface/api/ModelPortfoyView'
    '?son_donem=true&gecikmeli_fiyat=true'
)


def _get(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read().decode('utf-8')


def fetch_model_portfolio():
    """{'GARAN.IS': {'target_price', 'weight_pct', 'potential_pct', 'entry_date'}, ...}
    keyed with the same '.IS' suffix hisse.universe/hisse.data use."""
    payload = json.loads(_get(MODEL_PORTFOY_URL))
    hisses = (payload.get('datas') or [{}])[0].get('hisses') or []
    return {
        h['sembol'] + '.IS': dict(
            target_price=h.get('hedef_fiyat'),
            weight_pct=h.get('agirlik'),
            potential_pct=h.get('potansiyel'),
            entry_date=h.get('portfoy_giris_tarihi'),
        )
        for h in hisses if h.get('sembol')
    }
