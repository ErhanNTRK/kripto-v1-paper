import unittest
from unittest.mock import patch
from hisse import universe

SAMPLE_WIKITEXT = (
    "==Components==\nThere are 69 companies...\n"
    "{| class=\"wikitable sortable\" id=\"constituents\"\n"
    "! [[Ticker symbol]] !! Company !! Sector\n\n"
    "|-\n| AOS || [[A.O. Smith]] || Industrials \n"
    "|-\n| ABT || [[Abbott Laboratories]] || Health Care \n"
    "|-\n| BF.B || [[Brown-Forman]] (class B) || Consumer Staples \n"
    "|-\n| KO || [[Coca-Cola Co]] || Consumer Staples \n"
)


class UniverseTests(unittest.TestCase):
    def test_parse_aristocrats_tickers_reads_table_rows_only(self):
        tickers = universe.parse_aristocrats_tickers(SAMPLE_WIKITEXT)
        self.assertEqual(tickers, ['ABT', 'AOS', 'BF.B', 'KO'])

    def test_parse_aristocrats_tickers_ignores_header_row(self):
        tickers = universe.parse_aristocrats_tickers(SAMPLE_WIKITEXT)
        self.assertNotIn('Ticker', tickers)

    def test_bist_watchlist_entries_use_the_is_suffix(self):
        self.assertGreater(len(universe.BIST_WATCHLIST), 20)
        self.assertTrue(all(s.endswith('.IS') for s in universe.BIST_WATCHLIST))
        self.assertEqual(len(universe.BIST_WATCHLIST), len(set(universe.BIST_WATCHLIST)))

    def test_full_universe_combines_both_lists(self):
        with patch('hisse.universe.sp500_dividend_aristocrats', return_value=['KO', 'ABT']):
            result = universe.full_universe()
        self.assertEqual(result['us'], ['KO', 'ABT'])
        self.assertEqual(result['bist'], universe.BIST_WATCHLIST)


if __name__ == '__main__':
    unittest.main()
