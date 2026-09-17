import io
import unittest
import urllib.error
import zipfile
from unittest.mock import Mock

from crypto_v1.funding import _months_between, funding_rates


class Response:
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self): return self.body


def _zip_csv(rows):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as archive:
        lines = ['calc_time,funding_interval_hours,last_funding_rate']
        lines += [f'{t},8,{rate}' for t, rate in rows]
        archive.writestr('data.csv', '\n'.join(lines))
    return buf.getvalue()


class MonthsBetweenTests(unittest.TestCase):
    def test_single_month(self):
        start = 1_735_689_600_000  # 2025-01-01 UTC
        end = 1_736_000_000_000    # still January
        self.assertEqual(list(_months_between(start, end)), [(2025, 1)])

    def test_spans_a_year_boundary(self):
        start = 1_733_011_200_000  # 2024-12-01 UTC
        end = 1_735_776_000_000    # 2025-01-02 UTC
        self.assertEqual(list(_months_between(start, end)), [(2024, 12), (2025, 1)])


class FundingRatesTests(unittest.TestCase):
    def test_parses_events_within_range_and_sorts_them(self):
        rows = [(1000, 0.0001), (2000, -0.0002), (3000, 0.0003)]
        opener = Mock(return_value=Response(_zip_csv(rows)))
        result = funding_rates('BTCUSDT', 0, 4000, opener=opener, sleep=lambda s: None)
        self.assertEqual(result, [dict(t=1000, rate=0.0001), dict(t=2000, rate=-0.0002),
                                   dict(t=3000, rate=0.0003)])

    def test_excludes_events_outside_the_requested_window(self):
        rows = [(1000, 0.0001), (5000, 0.0002)]
        opener = Mock(return_value=Response(_zip_csv(rows)))
        result = funding_rates('BTCUSDT', 0, 4000, opener=opener, sleep=lambda s: None)
        self.assertEqual(result, [dict(t=1000, rate=0.0001)])

    def test_unpublished_month_returns_404_and_is_skipped_not_raised(self):
        opener = Mock(side_effect=urllib.error.HTTPError('url', 404, 'not found', {}, None))
        result = funding_rates('BTCUSDT', 0, 4000, opener=opener, sleep=lambda s: None)
        self.assertEqual(result, [])

    def test_non_404_http_error_propagates(self):
        opener = Mock(side_effect=urllib.error.HTTPError('url', 500, 'server error', {}, None))
        with self.assertRaises(urllib.error.HTTPError):
            funding_rates('BTCUSDT', 0, 4000, opener=opener, sleep=lambda s: None)


if __name__ == '__main__':
    unittest.main()
