import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from crypto_v1.github_worker import prepare_data, private_chat_id
from crypto_v1.research_v2 import FOUR_HOUR


class Response:
    def __init__(self, payload):
        self.payload = payload
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self):
        return json.dumps(self.payload).encode()


class PrepareDataTests(unittest.TestCase):
    def _run(self, runtime, existing_manifest=None):
        if existing_manifest is not None:
            (runtime / 'manifest.json').write_text(json.dumps(existing_manifest), encoding='utf-8')
        with patch('crypto_v1.github_worker.universe', return_value=['ETHUSDT']) as mock_universe, \
             patch('crypto_v1.github_worker.candles', return_value=[]), \
             patch('crypto_v1.github_worker.validate', return_value=[]):
            prepare_data({}, runtime, now=0)
        return mock_universe, json.loads((runtime / 'manifest.json').read_text(encoding='utf-8'))

    def test_no_cache_fetches_a_fresh_4h_universe(self):
        with tempfile.TemporaryDirectory() as tmp:
            mock_universe, manifest = self._run(Path(tmp))
        mock_universe.assert_called_once()
        self.assertEqual(manifest['timeframe'], '4h')
        self.assertEqual(manifest['symbols'], ['ETHUSDT'])

    def test_stale_pre_v2_cache_is_discarded_not_reused(self):
        stale = {'symbols': ['DOGEUSDT', '牛USDT'], 'timeframe': '15m'}
        with tempfile.TemporaryDirectory() as tmp:
            mock_universe, manifest = self._run(Path(tmp), existing_manifest=stale)
        # Old 15m/top-50 cache must trigger a fresh pick, not be trusted as-is.
        mock_universe.assert_called_once()
        self.assertEqual(manifest['timeframe'], '4h')
        self.assertEqual(manifest['symbols'], ['ETHUSDT'])

    def test_matching_4h_cache_is_reused_without_a_fresh_fetch(self):
        cached = {'symbols': ['SOLUSDT'], 'timeframe': '4h', 'source': 'x'}
        with tempfile.TemporaryDirectory() as tmp:
            mock_universe, manifest = self._run(Path(tmp), existing_manifest=cached)
        mock_universe.assert_not_called()
        self.assertEqual(manifest['symbols'], ['SOLUSDT'])

    def test_fetch_window_is_at_least_200_bars_for_ema200_to_ever_populate(self):
        # Regression: a 30-day (180-bar) window meant indicators.features'
        # ema200 (needs >=200 closes) never populated, so btc_up_ok/
        # btc_down_ok were always False and no entry signal could ever
        # fire -- caught only by zero trades/events in the live paper
        # state despite real market moves (18 Sep 2026), not by a test.
        now = 1_800_000_000_000
        with patch('crypto_v1.github_worker.universe', return_value=['ETHUSDT']), \
             patch('crypto_v1.github_worker.candles', return_value=[]) as mock_candles, \
             patch('crypto_v1.github_worker.validate', return_value=[]):
            with tempfile.TemporaryDirectory() as tmp:
                prepare_data({}, Path(tmp), now=now)
        start = mock_candles.call_args_list[0].args[1]
        self.assertGreaterEqual((now - start) / FOUR_HOUR, 200)


class GithubWorkerTests(unittest.TestCase):
    def test_resolves_exactly_one_private_start(self):
        payload = {"result": [{"message": {"text": "/start", "chat": {"id": 123, "type": "private"}}}]}
        with patch("urllib.request.urlopen", return_value=Response(payload)):
            self.assertEqual(private_chat_id("secret"), "123")

    def test_rejects_ambiguous_chats(self):
        payload = {"result": [
            {"message": {"text": "/start", "chat": {"id": 1, "type": "private"}}},
            {"message": {"text": "/start", "chat": {"id": 2, "type": "private"}}},
        ]}
        with patch("urllib.request.urlopen", return_value=Response(payload)):
            with self.assertRaises(RuntimeError):
                private_chat_id("secret")

    def test_ignores_groups_and_other_messages(self):
        payload = {"result": [
            {"message": {"text": "/start", "chat": {"id": 1, "type": "group"}}},
            {"message": {"text": "hello", "chat": {"id": 2, "type": "private"}}},
        ]}
        with patch("urllib.request.urlopen", return_value=Response(payload)):
            with self.assertRaises(RuntimeError):
                private_chat_id("secret")
