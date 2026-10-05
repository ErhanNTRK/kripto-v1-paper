import tempfile
import unittest
from pathlib import Path

from crypto_v1 import crash_alarm as ca

H = 3_600_000


class FakeMarket:
    """100 coins + BTC; `fall` of them sit `drop` under their 48h high; BTC's last close is set per test."""
    def __init__(self, now_ms):
        self.now, self.fall, self.drop, self.btc_low, self.btc_close = now_ms, 0, 0.25, 100.0, 100.0

    def bars(self, high, close, low=None):
        start = self.now - 49 * H
        rows = []
        for k in range(49):
            t = start + k * H
            c = close if k == 48 else high
            rows.append([t, high, high, low if (low is not None and k >= 46) else c * 0.99, c, 1, t + H - 1, 0])
        rows[-1][6] = self.now - 1  # the last bar has closed
        return rows

    def get(self, path, **params):
        if path == "ticker/24hr":
            return [{"symbol": f"C{i}USDT", "quoteVolume": str(1000 - i)} for i in range(100)] + \
                   [{"symbol": "USDCUSDT", "quoteVolume": "99999"}]
        symbol = params["symbol"]
        if symbol == "BTCUSDT":
            return self.bars(110.0, self.btc_close, low=self.btc_low)
        i = int(symbol[1:-4])
        return self.bars(10.0, 10.0 * (1 - self.drop) if i < self.fall else 10.0)


class CrashAlarmTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.now = 1_800_000_000_000
        self.market = FakeMarket(self.now)
        self.sent = []
        self.alarm = ca.CrashAlarm(Path(self.dir.name) / "crash.json", get=self.market.get,
                                   send=self.sent.append, clock=lambda: self.now / 1000)

    def tearDown(self):
        self.dir.cleanup()

    def step(self, hours):
        self.now += hours * H
        self.market.now = self.now

    def test_quiet_market_sends_nothing(self):
        self.market.fall = 10
        self.assertIsNone(self.alarm.tick())
        self.assertEqual(self.sent, [])

    def test_crash_then_rebound_alarms_once(self):
        self.market.fall, self.market.btc_low, self.market.btc_close = 40, 100.0, 100.5
        self.assertEqual(self.alarm.tick(), "crash")
        self.assertTrue(self.sent[0].startswith("[COKUS]"))
        self.step(2); self.market.btc_close = 104.0           # 4% off the low
        self.assertEqual(self.alarm.tick(), "rebound")
        self.assertTrue(self.sent[-1].startswith("[DONUS]"))
        self.step(1)                                          # still crashing, still bounced: quiet for 72h
        self.alarm.tick()
        self.assertEqual(len(self.sent), 2)

    def test_crash_expires_without_rebound(self):
        self.market.fall, self.market.btc_close = 40, 100.5
        self.alarm.tick()
        self.step(25); self.market.fall = 5                   # breadth healed, BTC never bounced 3%
        self.alarm.tick()
        self.assertIsNone(self.alarm._state()["crash_at"])
        self.assertEqual(len(self.sent), 1)

    def test_stablecoins_are_not_counted(self):
        down, total, _ = self.alarm.breadth(self.now)
        self.assertEqual(total, 100)


if __name__ == "__main__":
    unittest.main()
