import json
import unittest
import urllib.error
from unittest.mock import patch
from hisse import data


class HisseDataTests(unittest.TestCase):
    def setUp(self):
        data._crumb_cache.clear()

    def test_chart_returns_first_result(self):
        payload = {"chart": {"result": [{"meta": {"symbol": "KO"}}], "error": None}}
        with patch('hisse.data._get_json', return_value=payload) as get_json:
            result = data.chart('KO')
        self.assertEqual(result, {"meta": {"symbol": "KO"}})
        get_json.assert_called_once()

    def test_chart_raises_on_empty_result(self):
        payload = {"chart": {"result": None, "error": {"description": "Not found"}}}
        with patch('hisse.data._get_json', return_value=payload):
            with self.assertRaises(ValueError):
                data.chart('NOPE')

    def test_chart_requests_dividend_events_when_asked(self):
        payload = {"chart": {"result": [{}], "error": None}}
        with patch('hisse.data._get_json', return_value=payload) as get_json:
            data.chart('KO', dividends=True)
        params = get_json.call_args[0][1]
        self.assertEqual(params.get('events'), 'div')

    def test_crumb_token_is_fetched_once_and_cached(self):
        with patch('hisse.data._get', side_effect=[b'', b'  abc123  ']) as get:
            self.assertEqual(data.crumb_token(), 'abc123')
            self.assertEqual(data.crumb_token(), 'abc123')
        self.assertEqual(get.call_count, 2)  # seed + crumb, once total across both calls

    def test_crumb_token_tolerates_the_seed_urls_expected_404(self):
        # fc.yahoo.com returns 404 by design but still sets the needed
        # cookie; only the crumb call's own failure should be fatal.
        seed_error = urllib.error.HTTPError('https://fc.yahoo.com', 404, 'Not Found', {}, None)
        with patch('hisse.data._get', side_effect=[seed_error, b'  abc123  ']):
            self.assertEqual(data.crumb_token(), 'abc123')

    def test_quote_summary_includes_crumb_and_returns_first_result(self):
        payload = {"quoteSummary": {"result": [{"summaryDetail": {"dividendYield": {"raw": 0.04}}}], "error": None}}
        with patch('hisse.data.crumb_token', return_value='tok'), \
             patch('hisse.data._get_json', return_value=payload) as get_json:
            result = data.quote_summary('GARAN.IS', ['summaryDetail'])
        self.assertEqual(result['summaryDetail']['dividendYield']['raw'], 0.04)
        params = get_json.call_args[0][1]
        self.assertEqual(params['crumb'], 'tok')
        self.assertEqual(params['modules'], 'summaryDetail')

    def test_quote_summary_raises_on_empty_result(self):
        payload = {"quoteSummary": {"result": [], "error": {"description": "bad symbol"}}}
        with patch('hisse.data.crumb_token', return_value='tok'), \
             patch('hisse.data._get_json', return_value=payload):
            with self.assertRaises(ValueError):
                data.quote_summary('NOPE', ['summaryDetail'])

    def test_dividend_snapshot_extracts_raw_values_and_tolerates_missing_fields(self):
        modules = {
            "summaryDetail": {
                "dividendYield": {"raw": 0.0393, "fmt": "3.93%"},
                "dividendRate": {"raw": 5.27},
                "payoutRatio": {"raw": 0.1837},
                "fiveYearAvgDividendYield": {"raw": 2.1},
                "exDividendDate": {"raw": 1775520000},
            },
            "calendarEvents": {"dividendDate": {"raw": 1790812800}},
        }
        with patch('hisse.data.quote_summary', return_value=modules):
            snap = data.dividend_snapshot('GARAN.IS')
        self.assertEqual(snap['dividend_yield'], 0.0393)
        self.assertEqual(snap['payout_ratio'], 0.1837)
        self.assertEqual(snap['ex_dividend_date'], 1775520000)
        self.assertEqual(snap['dividend_payment_date'], 1790812800)

    def test_dividend_snapshot_falls_back_to_calendar_events_for_ex_date(self):
        modules = {"summaryDetail": {}, "calendarEvents": {"exDividendDate": {"raw": 111}}}
        with patch('hisse.data.quote_summary', return_value=modules):
            snap = data.dividend_snapshot('KO')
        self.assertEqual(snap['ex_dividend_date'], 111)
        self.assertIsNone(snap['dividend_yield'])

    def test_dividend_history_parses_and_sorts_events(self):
        result = {"events": {"dividends": {
            "200": {"amount": 0.5, "date": 200},
            "100": {"amount": 0.4, "date": 100},
        }}}
        with patch('hisse.data.chart', return_value=result):
            history = data.dividend_history('KO')
        self.assertEqual(history, [
            {"date": 100, "amount": 0.4},
            {"date": 200, "amount": 0.5},
        ])

    def test_dividend_history_empty_when_no_events(self):
        with patch('hisse.data.chart', return_value={}):
            self.assertEqual(data.dividend_history('KO'), [])


if __name__ == '__main__':
    unittest.main()
