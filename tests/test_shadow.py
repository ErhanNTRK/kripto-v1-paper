import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from crypto_v1 import shadow

H4 = 4 * 3600 * 1000
CONFIG = {"trailing_atr": 4.0, "btc_filter": "loose"}


def bars(prices, start=0):
    """Candles from (open, high, low, close) tuples, one 4h bar apart."""
    return [{"t": start + i * H4, "o": o, "h": h, "l": l, "c": c, "v": 1.0}
            for i, (o, h, l, c) in enumerate(prices)]


def fake_features(rows, config):
    return [dict(r, atr=1.0) for r in rows]


class RecordTests(unittest.TestCase):
    """Signals skipped for lack of capital are kept, once each."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "shadow.json"
        self.candidate = {"symbol": "FETUSDT", "created_at": 1790352000000, "close": 0.2393,
                          "stop": 0.2185, "breaks_up": 0}

    def test_a_size_skip_is_kept_once(self):
        self.assertTrue(shadow.record(self.path, "4", self.candidate, "long",
                                      "risk target below Binance minimum", 1))
        self.assertFalse(shadow.record(self.path, "4", self.candidate, "long",
                                       "risk target below Binance minimum", 2))
        kept = shadow._load(self.path)
        self.assertEqual(len(kept), 1)
        self.assertEqual((kept[0]["entry"], kept[0]["stop"], kept[0]["tag"]), (0.2393, 0.2185, "4"))

    def test_other_rejections_are_not_kept(self):
        for reason in ("entry_price_moved", "position_limit", "awaiting_approval", "already_holding_symbol"):
            self.assertFalse(shadow.record(self.path, "4", self.candidate, "long", reason, 1))
        self.assertEqual(shadow._load(self.path), [])


@patch("crypto_v1.shadow.ShortWindowLongModel.features", side_effect=fake_features)
class ReplayTests(unittest.TestCase):
    """The bot's own exit rules, played forward on closed bars."""

    TRADE = {"symbol": "XUSDT", "signal_time": H4, "entry": 100.0, "stop": 98.0}
    BTC = bars([(1, 1, 1, 1)] * 10)

    def _replay(self, prices, sell=False):
        with patch("crypto_v1.shadow.ShortWindowLongModel.sell", return_value=sell):
            return shadow.replay(self.TRADE, bars(prices), self.BTC, CONFIG, 2.0, H4)

    def test_the_stop_is_hit_intrabar(self, _):
        result = self._replay([(99, 100, 99, 100), (100, 101, 97, 98.5)])
        self.assertEqual((result["status"], result["reason"], result["r"]), ("closed", "stop", -1.0))

    def test_a_trend_exit_closes_at_the_bar_close(self, _):
        result = self._replay([(99, 100, 99, 100), (100, 103, 99.5, 102)], sell=True)
        self.assertEqual(result["status"], "closed")
        self.assertEqual(result["reason"], "profit_signal")
        self.assertEqual(result["r"], 1.0)

    def test_the_trailing_exit_after_a_run(self, _):
        # Up to 112 (6R), then a close 4 ATR under the high: trailing exit.
        result = self._replay([(99, 100, 99, 100), (100, 112, 100, 111), (111, 111, 107.5, 107.9)])
        self.assertEqual(result["reason"], "trailing_profit")
        self.assertAlmostEqual(result["r"], 3.95)

    def test_a_trade_still_running_is_marked_open(self, _):
        result = self._replay([(99, 100, 99, 100), (100, 101.5, 99.5, 101)])
        self.assertEqual(result["status"], "open")
        self.assertEqual(result["r"], 0.5)

    def test_the_signal_bar_itself_is_not_replayed(self, _):
        # The first bar (t=0) is the signal bar; its low under the stop must not count.
        result = self._replay([(99, 100, 90, 100), (100, 101, 99, 100.5)])
        self.assertEqual(result["status"], "open")


class SummaryTests(unittest.TestCase):
    def test_the_morning_line(self):
        trade = {"symbol": "FETUSDT", "entry": 0.2, "stop": 0.18}
        line = shadow.summary_line([(trade, {"status": "closed", "exit": 0.22, "r": 1.0}),
                                    (trade, {"status": "open", "exit": 0.19, "r": -0.5})])
        self.assertIn("2 islem", line)
        self.assertIn("+0.5R", line)
        self.assertIn("1 kapandi, 1 suruyor", line)

    def test_nothing_kept_means_no_line(self):
        self.assertIsNone(shadow.summary_line([]))

    def test_ltc_is_valued_at_its_own_20_usdt_minimum(self):
        trade = {"symbol": "LTCUSDT", "entry": 70.0}
        self.assertAlmostEqual(shadow.usdt_at_minimum(trade, {"exit": 77.0}), 20 * 1.03 * 0.1 - 20 * 1.03 * 0.002)


if __name__ == "__main__":
    unittest.main()
