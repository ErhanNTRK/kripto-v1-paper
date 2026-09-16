import unittest
from crypto_v1.data import validate, BINANCE_CODE, INTERVAL


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
        for ms in (900_000, 3_600_000, 14_400_000, 21_600_000, 86_400_000):
            self.assertIn(ms, BINANCE_CODE)


if __name__ == '__main__':
    unittest.main()
