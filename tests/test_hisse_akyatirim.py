import json
import unittest
from unittest.mock import patch
from hisse import akyatirim

SAMPLE_PAYLOAD = {
    "datas": [{
        "hisses": [
            {"sembol": "GARAN", "tanim": "GARANTI BANKASI", "portfoy_giris_tarihi": "2026-03-18T09:18:31",
             "hedef_fiyat": 181.5, "agirlik": 9.0, "potansiyel": 50.37},
            {"sembol": "KCHOL", "tanim": "KOC HOLDING", "portfoy_giris_tarihi": "2023-03-16T00:00:00",
             "hedef_fiyat": 348.0, "agirlik": 14.0, "potansiyel": 70.67},
        ]
    }]
}


class AkYatirimTests(unittest.TestCase):
    def test_fetch_model_portfolio_keys_by_symbol_with_is_suffix(self):
        with patch('hisse.akyatirim._get', return_value=json.dumps(SAMPLE_PAYLOAD)):
            result = akyatirim.fetch_model_portfolio()
        self.assertIn('GARAN.IS', result)
        self.assertIn('KCHOL.IS', result)

    def test_fetch_model_portfolio_parses_expected_fields(self):
        with patch('hisse.akyatirim._get', return_value=json.dumps(SAMPLE_PAYLOAD)):
            result = akyatirim.fetch_model_portfolio()
        garan = result['GARAN.IS']
        self.assertEqual(garan['target_price'], 181.5)
        self.assertEqual(garan['weight_pct'], 9.0)
        self.assertAlmostEqual(garan['potential_pct'], 50.37)

    def test_fetch_model_portfolio_handles_empty_payload(self):
        with patch('hisse.akyatirim._get', return_value=json.dumps({"datas": []})):
            result = akyatirim.fetch_model_portfolio()
        self.assertEqual(result, {})


if __name__ == '__main__':
    unittest.main()
