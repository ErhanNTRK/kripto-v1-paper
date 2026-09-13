import unittest
from crypto_v1.live_signal import pending_candidates


class LiveSignalTests(unittest.TestCase):
    def test_only_current_engine_pending_candidate_is_returned(self):
        saved = {"state": {"pending_buys": {"SOLUSDT": {"stop": 95, "score": 2}},
                           "events": [
                               {"type": "AL_ADAYI", "time": 1000, "symbol": "OLDUSDT", "close": 1},
                               {"type": "AL_ADAYI", "time": 500000, "symbol": "SOLUSDT", "close": 100}]}}
        result = pending_candidates(saved, 501000, {"signal_confirmation_expiry_minutes": 10})
        self.assertEqual(result, [{"symbol": "SOLUSDT", "created_at": 500000,
                                   "close": 100.0, "stop": 95.0}])

    def test_expired_candidate_is_rejected(self):
        saved = {"state": {"pending_buys": {"SOLUSDT": {"stop": 95}},
                           "events": [{"type": "AL_ADAYI", "time": 1,
                                       "symbol": "SOLUSDT", "close": 100}]}}
        self.assertEqual(pending_candidates(saved, 700000,
                                             {"signal_confirmation_expiry_minutes": 10}), [])

    def test_duplicate_events_collapse_by_symbol(self):
        saved = {"state": {"pending_buys": {"SOLUSDT": {"stop": 95}},
                           "events": [{"type": "AL_ADAYI", "time": 1000,
                                       "symbol": "SOLUSDT", "close": 99},
                                      {"type": "AL_ADAYI", "time": 2000,
                                       "symbol": "SOLUSDT", "close": 100}]}}
        result = pending_candidates(saved, 3000, {"signal_confirmation_expiry_minutes": 10})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["close"], 100)
