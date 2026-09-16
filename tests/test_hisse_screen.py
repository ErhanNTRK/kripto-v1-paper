import unittest
from hisse import screen


def snap(**kw):
    base = dict(symbol='KO', dividend_yield=0.03, dividend_rate=0.5, payout_ratio=0.5,
                five_year_avg_yield=0.025, ex_dividend_date=1775520000, dividend_payment_date=None)
    base.update(kw)
    return base


class DividendQualityTests(unittest.TestCase):
    def test_passes_with_healthy_numbers_and_history(self):
        ok, reasons = screen.dividend_quality(snap(), [{}, {}, {}])
        self.assertTrue(ok)
        self.assertEqual(reasons, [])

    def test_rejects_unknown_yield(self):
        ok, reasons = screen.dividend_quality(snap(dividend_yield=None), [{}, {}])
        self.assertFalse(ok)
        self.assertIn('yield unknown', reasons)

    def test_rejects_absurdly_high_yield_as_distress_signal(self):
        ok, reasons = screen.dividend_quality(snap(dividend_yield=0.25), [{}, {}])
        self.assertFalse(ok)
        self.assertTrue(any('yield out of range' in r for r in reasons))

    def test_rejects_excessive_payout_ratio(self):
        ok, reasons = screen.dividend_quality(snap(payout_ratio=0.95), [{}, {}])
        self.assertFalse(ok)
        self.assertTrue(any('payout ratio' in r for r in reasons))

    def test_rejects_thin_history(self):
        ok, reasons = screen.dividend_quality(snap(), [{}])
        self.assertFalse(ok)
        self.assertIn('not enough dividend history', reasons)

    def test_payout_ratio_none_does_not_fail_the_screen(self):
        ok, reasons = screen.dividend_quality(snap(payout_ratio=None), [{}, {}])
        self.assertTrue(ok)


class YieldIsNotablyHighTests(unittest.TestCase):
    def test_true_when_well_above_own_five_year_average(self):
        self.assertTrue(screen.yield_is_notably_high(snap(dividend_yield=0.05, five_year_avg_yield=0.03)))

    def test_false_when_close_to_average(self):
        self.assertFalse(screen.yield_is_notably_high(snap(dividend_yield=0.031, five_year_avg_yield=0.03)))

    def test_false_when_average_unknown(self):
        self.assertFalse(screen.yield_is_notably_high(snap(five_year_avg_yield=None)))


class TrendOkTests(unittest.TestCase):
    def test_false_with_too_little_history(self):
        self.assertFalse(screen.trend_ok([100] * 50))

    def test_true_when_above_sma_and_rising(self):
        closes = [100.0] * 180 + list(range(100, 120))  # 200 bars, rising tail
        self.assertTrue(screen.trend_ok(closes))

    def test_false_when_below_sma(self):
        closes = [120.0] * 180 + list(range(120, 100, -1))  # falling into a low price
        self.assertFalse(screen.trend_ok(closes))


class MessageTests(unittest.TestCase):
    def test_high_yield_message_includes_key_numbers_and_disclaimer(self):
        msg = screen.high_yield_message('GARAN.IS', snap(dividend_yield=0.0393, five_year_avg_yield=0.021, payout_ratio=0.18))
        self.assertIn('GARAN.IS', msg)
        self.assertIn('3.93%', msg)
        self.assertIn('alim tavsiyesi degildir', msg)

    def test_last_buy_date_message_formats_date_and_warns_about_drop(self):
        msg = screen.last_buy_date_message('KO', snap(ex_dividend_date=1775520000), now_s=1774000000)
        self.assertIn('07.04.2026', msg)
        self.assertIn('NORMALDIR', msg)

    def test_post_ex_dividend_message_is_honest_about_no_free_money(self):
        msg = screen.post_ex_dividend_message('KO', snap(dividend_rate=0.49))
        self.assertIn('~0.49', msg)
        self.assertIn('temettu avciligi', msg)
        self.assertIn('genelde ekstra', msg)


if __name__ == '__main__':
    unittest.main()
