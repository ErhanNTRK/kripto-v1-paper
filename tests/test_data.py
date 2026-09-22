import unittest
from unittest.mock import patch
from crypto_v1.data import validate, BINANCE_CODE, INTERVAL, futures_tradable_symbols, universe


def bar(t, o=100.0, h=101.0, l=99.0, c=100.0, v=10.0):
    return dict(t=t, o=o, h=h, l=l, c=c, v=v)


class DataIntervalTests(unittest.TestCase):
    def test_validate_defaults_to_15m_spacing(self):
        rows = [bar(0), bar(INTERVAL)]
        self.assertEqual(validate(rows), rows)
        with self.assertRaises(ValueError):
            validate([bar(0), bar(2 * INTERVAL)])

    def test_validate_accepts_a_coarser_interval(self):
        four_hour = 14_400_000
        rows = [bar(0), bar(four_hour), bar(2 * four_hour)]
        self.assertEqual(validate(rows, four_hour), rows)
        # 15m-spaced rows are invalid at 4h granularity.
        with self.assertRaises(ValueError):
            validate([bar(0), bar(INTERVAL)], four_hour)

    def test_binance_code_covers_intervals_used_in_this_project(self):
        for ms in (900_000, 3_600_000, 7_200_000, 14_400_000, 21_600_000, 86_400_000):
            self.assertIn(ms, BINANCE_CODE)


class FuturesTradableSymbolsTests(unittest.TestCase):
    def test_keeps_only_trading_usdt_perpetuals(self):
        info = {'symbols': [
            {'symbol': 'BTCUSDT', 'quoteAsset': 'USDT', 'contractType': 'PERPETUAL', 'status': 'TRADING'},
            {'symbol': 'BTCUSDT_240329', 'quoteAsset': 'USDT', 'contractType': 'CURRENT_QUARTER', 'status': 'TRADING'},
            {'symbol': 'ETHBUSD', 'quoteAsset': 'BUSD', 'contractType': 'PERPETUAL', 'status': 'TRADING'},
            {'symbol': 'DELISTEDUSDT', 'quoteAsset': 'USDT', 'contractType': 'PERPETUAL', 'status': 'CLOSE'},
        ]}
        with patch('crypto_v1.data.futures_get', return_value=info):
            self.assertEqual(futures_tradable_symbols(), {'BTCUSDT'})


class UniverseTests(unittest.TestCase):
    CONFIG = {'excluded_bases': [], 'top_n': 50}

    def test_a_spot_only_symbol_with_no_futures_contract_is_excluded(self):
        # Live-observed 22 Sep 2026: FETUSDT/MARSCOINUSDT passed every Spot
        # eligibility check, got detected as real candidates, then every
        # entry attempt failed with Binance -1121 "Invalid symbol" because
        # neither has a USDT-M perpetual contract -- forever, since the
        # same signal kept re-qualifying each scan.
        exchange_info = {'symbols': [
            {'symbol': 'BTCUSDT', 'quoteAsset': 'USDT', 'status': 'TRADING',
             'isSpotTradingAllowed': True, 'baseAsset': 'BTC'},
            {'symbol': 'FETUSDT', 'quoteAsset': 'USDT', 'status': 'TRADING',
             'isSpotTradingAllowed': True, 'baseAsset': 'FET'},
        ]}
        tickers = [
            {'symbol': 'BTCUSDT', 'quoteVolume': '1000'},
            {'symbol': 'FETUSDT', 'quoteVolume': '2000'},  # higher volume, would rank first
        ]
        with patch('crypto_v1.data.get', side_effect=lambda path, params=None:
                   exchange_info if path == 'exchangeInfo' else tickers), \
             patch('crypto_v1.data.futures_tradable_symbols', return_value={'BTCUSDT'}):
            self.assertEqual(universe(self.CONFIG), ['BTCUSDT'])


if __name__ == '__main__':
    unittest.main()
