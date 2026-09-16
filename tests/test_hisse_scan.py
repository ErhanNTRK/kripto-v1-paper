import unittest
from unittest.mock import patch
from hisse import scan

DAY = 86400
GOOD_SNAP = dict(symbol='KO', dividend_yield=0.05, dividend_rate=0.5, payout_ratio=0.5,
                  five_year_avg_yield=0.03, ex_dividend_date=None, dividend_payment_date=None)
RISING_CLOSES = [100.0] * 180 + list(range(100, 120))
CHART_RESULT = dict(indicators=dict(quote=[dict(close=RISING_CLOSES)]))


class ScanSymbolTests(unittest.TestCase):
    def test_flags_high_yield_when_quality_trend_and_yield_all_pass(self):
        with patch('hisse.data.dividend_snapshot', return_value=GOOD_SNAP), \
             patch('hisse.data.dividend_history', return_value=[{}, {}, {}]), \
             patch('hisse.data.chart', return_value=CHART_RESULT):
            events = scan.scan_symbol('KO', now_s=0)
        kinds = [e['kind'] for e in events]
        self.assertIn('high_yield', kinds)

    def test_no_high_yield_event_when_trend_is_down(self):
        falling = dict(indicators=dict(quote=[dict(close=[120.0] * 200)]))
        with patch('hisse.data.dividend_snapshot', return_value=GOOD_SNAP), \
             patch('hisse.data.dividend_history', return_value=[{}, {}, {}]), \
             patch('hisse.data.chart', return_value=falling):
            events = scan.scan_symbol('KO', now_s=0)
        self.assertEqual([e for e in events if e['kind'] == 'high_yield'], [])

    def test_last_buy_date_event_within_window(self):
        snap = dict(GOOD_SNAP, ex_dividend_date=3 * DAY)
        with patch('hisse.data.dividend_snapshot', return_value=snap), \
             patch('hisse.data.dividend_history', return_value=[{}, {}]), \
             patch('hisse.data.chart', return_value=CHART_RESULT):
            events = scan.scan_symbol('KO', now_s=0)
        last_buy = [e for e in events if e['kind'] == 'last_buy_date']
        self.assertEqual(len(last_buy), 1)
        self.assertEqual(last_buy[0]['dedupe_scope'], 'day')

    def test_post_ex_dividend_event_shortly_after(self):
        snap = dict(GOOD_SNAP, ex_dividend_date=-1 * DAY)
        with patch('hisse.data.dividend_snapshot', return_value=snap), \
             patch('hisse.data.dividend_history', return_value=[{}, {}]), \
             patch('hisse.data.chart', return_value=CHART_RESULT):
            events = scan.scan_symbol('KO', now_s=0)
        post = [e for e in events if e['kind'] == 'post_ex_dividend']
        self.assertEqual(len(post), 1)
        self.assertEqual(post[0]['dedupe_scope'], 'once')
        self.assertEqual(post[0]['dedupe_id'], -1 * DAY)

    def test_no_date_event_far_from_ex_date(self):
        snap = dict(GOOD_SNAP, ex_dividend_date=60 * DAY)
        with patch('hisse.data.dividend_snapshot', return_value=snap), \
             patch('hisse.data.dividend_history', return_value=[{}, {}]), \
             patch('hisse.data.chart', return_value=CHART_RESULT):
            events = scan.scan_symbol('KO', now_s=0)
        self.assertEqual([e for e in events if e['kind'] in ('last_buy_date', 'post_ex_dividend')], [])


class PriceInfoTests(unittest.TestCase):
    def test_computes_realized_one_year_change_and_reads_meta_fields(self):
        chart_result = dict(
            meta=dict(regularMarketPrice=127.8, currency='TRY', fiftyTwoWeekLow=90.0, fiftyTwoWeekHigh=140.0),
        )
        price = scan._price_info(chart_result, closes=[100.0, 142.0])
        self.assertEqual(price['current'], 127.8)
        self.assertEqual(price['currency'], 'TRY')
        self.assertAlmostEqual(price['change_1y_pct'], 0.42)
        self.assertEqual(price['week52_low'], 90.0)

    def test_change_1y_pct_is_none_with_too_little_history(self):
        price = scan._price_info(dict(meta={}), closes=[100.0])
        self.assertIsNone(price['change_1y_pct'])


class ScanAllTests(unittest.TestCase):
    def test_one_bad_symbol_does_not_stop_the_others(self):
        def fake_scan_symbol(symbol, now_s, ak_portfolio=None):
            if symbol == 'BAD':
                raise ValueError('no data')
            return [dict(kind='high_yield', dedupe_scope='week', message='ok')]
        with patch('hisse.scan.akyatirim.fetch_model_portfolio', return_value={}), \
             patch('hisse.scan.scan_symbol', side_effect=fake_scan_symbol):
            results, errors = scan.scan_all(['GOOD', 'BAD'], now_s=0)
        self.assertIn('GOOD', results)
        self.assertEqual(errors, {'BAD': 'no data'})

    def test_symbols_with_no_events_are_omitted(self):
        with patch('hisse.scan.akyatirim.fetch_model_portfolio', return_value={}), \
             patch('hisse.scan.scan_symbol', return_value=[]):
            results, errors = scan.scan_all(['X'], now_s=0)
        self.assertEqual(results, {})
        self.assertEqual(errors, {})

    def test_ak_portfolio_fetch_failure_does_not_block_the_scan(self):
        with patch('hisse.scan.akyatirim.fetch_model_portfolio', side_effect=RuntimeError('down')), \
             patch('hisse.scan.scan_symbol', return_value=[]) as mock_scan:
            results, errors = scan.scan_all(['X'], now_s=0)
        self.assertEqual(results, {})
        self.assertEqual(errors, {})
        mock_scan.assert_called_once_with('X', 0, {})

    def test_ak_portfolio_is_fetched_once_and_passed_to_every_symbol(self):
        ak = {'GARAN.IS': {'target_price': 181.5}}
        with patch('hisse.scan.akyatirim.fetch_model_portfolio', return_value=ak), \
             patch('hisse.scan.scan_symbol', return_value=[]) as mock_scan:
            scan.scan_all(['GARAN.IS', 'KO'], now_s=0)
        mock_scan.assert_any_call('GARAN.IS', 0, ak)
        mock_scan.assert_any_call('KO', 0, ak)


if __name__ == '__main__':
    unittest.main()
