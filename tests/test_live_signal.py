import unittest
from crypto_v1.live_signal import (RUNTIME_STATE, RUNTIME_STATE_2H, RUNTIME_STATE_SHORT,
                                    RUNTIME_STATE_SHORT_2H, fetch_runtime_state,
                                    fetch_runtime_state_short, isolate_candidate,
                                    isolate_short_candidate, pending_candidates,
                                    pending_short_candidates)


class FetchUrlOverrideTests(unittest.TestCase):
    """The 2H system (18 Sep 2026) reads its own candidate files via a url
    override -- these must default to the original 4H URLs (backward
    compatible) but fetch whatever url is actually passed."""

    def test_default_url_is_the_4h_state_file(self):
        seen = {}
        def opener(request, timeout):
            seen['url'] = request.full_url
            class Resp:
                def __enter__(self): return self
                def __exit__(self, *a): return False
                def read(self): return b'{}'
            return Resp()
        from unittest.mock import patch
        with patch('json.load', return_value={'state': {}}):
            fetch_runtime_state(opener=opener)
        self.assertEqual(seen['url'], RUNTIME_STATE)

    def test_url_override_is_used_when_given(self):
        seen = {}
        def opener(request, timeout):
            seen['url'] = request.full_url
            class Resp:
                def __enter__(self): return self
                def __exit__(self, *a): return False
                def read(self): return b'{}'
            return Resp()
        from unittest.mock import patch
        with patch('json.load', return_value={'state': {}}):
            fetch_runtime_state(opener=opener, url=RUNTIME_STATE_2H)
        self.assertEqual(seen['url'], RUNTIME_STATE_2H)

    def test_short_fetch_also_honors_a_url_override(self):
        seen = {}
        def opener(request, timeout):
            seen['url'] = request.full_url
            class Resp:
                def __enter__(self): return self
                def __exit__(self, *a): return False
                def read(self): return b'{}'
            return Resp()
        from unittest.mock import patch
        with patch('json.load', return_value={'state': {}}):
            fetch_runtime_state_short(opener=opener, url=RUNTIME_STATE_SHORT_2H)
        self.assertEqual(seen['url'], RUNTIME_STATE_SHORT_2H)

    def test_2h_urls_are_distinct_from_4h_urls(self):
        self.assertNotEqual(RUNTIME_STATE, RUNTIME_STATE_2H)
        self.assertNotEqual(RUNTIME_STATE_SHORT, RUNTIME_STATE_SHORT_2H)


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


class PendingShortCandidatesTests(unittest.TestCase):
    def test_only_current_pending_short_is_returned(self):
        saved = {"state": {"pending_shorts": {"SOLUSDT": {"stop": 105, "leverage": 3}},
                           "events": [
                               {"type": "SHORT_ADAYI", "time": 1000, "symbol": "OLDUSDT", "close": 1},
                               {"type": "SHORT_ADAYI", "time": 500000, "symbol": "SOLUSDT", "close": 100}]}}
        result = pending_short_candidates(saved, 501000, {"signal_confirmation_expiry_minutes": 10})
        self.assertEqual(result, [{"symbol": "SOLUSDT", "created_at": 500000,
                                   "close": 100.0, "stop": 105.0, "leverage": 3}])

    def test_expired_short_candidate_is_rejected(self):
        saved = {"state": {"pending_shorts": {"SOLUSDT": {"stop": 105, "leverage": 3}},
                           "events": [{"type": "SHORT_ADAYI", "time": 1,
                                       "symbol": "SOLUSDT", "close": 100}]}}
        self.assertEqual(pending_short_candidates(saved, 700000,
                                                   {"signal_confirmation_expiry_minutes": 10}), [])

    def test_long_candidates_never_leak_into_short_results(self):
        saved = {"state": {"pending_shorts": {}, "pending_buys": {"SOLUSDT": {"stop": 95}},
                           "events": [{"type": "AL_ADAYI", "time": 1000,
                                       "symbol": "SOLUSDT", "close": 100}]}}
        self.assertEqual(pending_short_candidates(saved, 1500,
                                                   {"signal_confirmation_expiry_minutes": 10}), [])


class IsolateCandidateTests(unittest.TestCase):
    """Multiple simultaneously pending candidates must be processable one
    at a time -- each isolated view should look, to pending_candidates,
    exactly like a state with only that one candidate pending."""

    def test_isolate_candidate_keeps_only_the_named_symbol(self):
        saved = {"state": {"pending_buys": {"SOLUSDT": {"stop": 95}, "ETHUSDT": {"stop": 2400}},
                           "events": [{"type": "AL_ADAYI", "time": 1000, "symbol": "SOLUSDT", "close": 100},
                                      {"type": "AL_ADAYI", "time": 1000, "symbol": "ETHUSDT", "close": 2500}]}}
        isolated = isolate_candidate(saved, "SOLUSDT")
        result = pending_candidates(isolated, 1500, {"signal_confirmation_expiry_minutes": 10})
        self.assertEqual(result, [{"symbol": "SOLUSDT", "created_at": 1000, "close": 100.0, "stop": 95.0}])

    def test_isolate_candidate_for_a_symbol_not_present_yields_nothing(self):
        saved = {"state": {"pending_buys": {"SOLUSDT": {"stop": 95}},
                           "events": [{"type": "AL_ADAYI", "time": 1000, "symbol": "SOLUSDT", "close": 100}]}}
        isolated = isolate_candidate(saved, "ETHUSDT")
        self.assertEqual(pending_candidates(isolated, 1500, {"signal_confirmation_expiry_minutes": 10}), [])


class IsolateShortCandidateTests(unittest.TestCase):
    def test_isolate_short_candidate_keeps_only_the_named_symbol(self):
        saved = {"state": {"pending_shorts": {"SOLUSDT": {"stop": 105, "leverage": 3},
                                              "ETHUSDT": {"stop": 2600, "leverage": 5}},
                           "events": [{"type": "SHORT_ADAYI", "time": 1000, "symbol": "SOLUSDT", "close": 100},
                                      {"type": "SHORT_ADAYI", "time": 1000, "symbol": "ETHUSDT", "close": 2500}]}}
        isolated = isolate_short_candidate(saved, "ETHUSDT")
        result = pending_short_candidates(isolated, 1500, {"signal_confirmation_expiry_minutes": 10})
        self.assertEqual(result, [{"symbol": "ETHUSDT", "created_at": 1000,
                                   "close": 2500.0, "stop": 2600.0, "leverage": 5}])


class StrongestFirstTests(unittest.TestCase):
    """22 Sep 2026: auto_enter takes candidates in list order until margin
    or position capacity runs out, so the list must be strongest first
    (breakout count, then volume ratio), not alphabetical."""

    def _saved(self, pending, key="pending_buys", kind="AL_ADAYI"):
        return {"state": {key: pending, "events": [
            {"type": kind, "time": 1000, "symbol": s, "close": 100} for s in pending]}}

    def test_long_candidates_are_ordered_by_breaks_then_volume(self):
        saved = self._saved({
            "AAAUSDT": {"stop": 90, "breaks_up": 1, "score": 5.0},
            "BBBUSDT": {"stop": 90, "breaks_up": 3, "score": 1.0},
            "CCCUSDT": {"stop": 90, "breaks_up": 3, "score": 2.0}})
        result = pending_candidates(saved, 2000, {"signal_confirmation_expiry_minutes": 10})
        self.assertEqual([c["symbol"] for c in result], ["CCCUSDT", "BBBUSDT", "AAAUSDT"])

    def test_short_candidates_are_ordered_by_strength(self):
        saved = self._saved({
            "AAAUSDT": {"stop": 110, "leverage": 3, "breaks_down": 1, "score": 1.0},
            "BBBUSDT": {"stop": 110, "leverage": 3, "breaks_down": 2, "score": 1.0}},
            key="pending_shorts", kind="SHORT_ADAYI")
        result = pending_short_candidates(saved, 2000, {"signal_confirmation_expiry_minutes": 10})
        self.assertEqual([c["symbol"] for c in result], ["BBBUSDT", "AAAUSDT"])

    def test_equal_or_missing_strength_falls_back_to_symbol_order(self):
        saved = self._saved({"BBBUSDT": {"stop": 90}, "AAAUSDT": {"stop": 90}})
        result = pending_candidates(saved, 2000, {"signal_confirmation_expiry_minutes": 10})
        self.assertEqual([c["symbol"] for c in result], ["AAAUSDT", "BBBUSDT"])
