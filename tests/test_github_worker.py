import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from crypto_v1.github_worker import (local_tick, prepare_data, private_chat_id,
                                     write_2h_signal_state, write_short_state)
from crypto_v1.research_v2 import FOUR_HOUR, TWO_HOUR


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


class LocalTickTests(unittest.TestCase):
    """local_tick (21 Sep 2026) runs the same detection pipeline as main(),
    but from render_web's own periodic loop instead of GitHub Actions'
    schedule trigger -- live-observed to actually fire every 2-5 hours
    despite being configured for every 15 minutes. These tests exercise
    its own new orchestration only (relaxed-limits override, retry-once
    behavior); prepare_data/write_short_state/write_2h_signal_state/
    deliver_* already have their own coverage above and in
    test_telegram.py, so they're mocked here."""

    def test_relaxes_paper_limits_and_runs_the_full_pipeline_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            with patch('crypto_v1.github_worker.get', return_value={'serverTime': 800 * FOUR_HOUR}), \
                 patch('crypto_v1.github_worker.prepare_data', return_value=runtime / 'data') as mock_prepare, \
                 patch('crypto_v1.github_worker.tick') as mock_tick, \
                 patch('crypto_v1.github_worker.write_short_state') as mock_short, \
                 patch('crypto_v1.github_worker.write_2h_signal_state') as mock_2h, \
                 patch('crypto_v1.github_worker.deliver_paper_events', return_value=0) as mock_dp, \
                 patch('crypto_v1.github_worker.deliver_short_events', return_value=0) as mock_ds, \
                 patch('crypto_v1.github_worker.deliver_fresh_events', return_value=0) as mock_df:
                local_tick(runtime)
        # The paper engine's own daily-loss/consecutive-loss halt must never
        # be allowed to silently starve the live candidate feed -- same
        # override paper.yml applies via PAPER_RELAX_LIMITS=1.
        used_config = mock_prepare.call_args.args[0]
        self.assertEqual(used_config['daily_loss_fraction'], 0.05)
        self.assertEqual(used_config['max_consecutive_losses'], 100000)
        mock_tick.assert_called_once()
        mock_short.assert_called_once()
        mock_2h.assert_called_once()
        mock_dp.assert_called_once()
        mock_ds.assert_called_once()
        self.assertEqual(mock_df.call_count, 2)  # 2H long + 2H short

    def test_retries_once_on_new_paper_state_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            (runtime / 'state-relaxed.json').write_text('{}', encoding='utf-8')
            with patch('crypto_v1.github_worker.get', return_value={'serverTime': 800 * FOUR_HOUR}), \
                 patch('crypto_v1.github_worker.prepare_data', return_value=runtime / 'data'), \
                 patch('crypto_v1.github_worker.tick',
                      side_effect=[ValueError('new paper state required'), None]) as mock_tick, \
                 patch('crypto_v1.github_worker.write_short_state'), \
                 patch('crypto_v1.github_worker.write_2h_signal_state'), \
                 patch('crypto_v1.github_worker.deliver_paper_events', return_value=0), \
                 patch('crypto_v1.github_worker.deliver_short_events', return_value=0), \
                 patch('crypto_v1.github_worker.deliver_fresh_events', return_value=0):
                local_tick(runtime)
            self.assertEqual(mock_tick.call_count, 2)
            self.assertFalse((runtime / 'state-relaxed.json').exists())

    def test_reraises_unrelated_value_errors_without_retrying(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            with patch('crypto_v1.github_worker.get', return_value={'serverTime': 800 * FOUR_HOUR}), \
                 patch('crypto_v1.github_worker.prepare_data', return_value=runtime / 'data'), \
                 patch('crypto_v1.github_worker.tick', side_effect=ValueError('unrelated')) as mock_tick:
                with self.assertRaises(ValueError):
                    local_tick(runtime)
            self.assertEqual(mock_tick.call_count, 1)


class WriteShortStateTests(unittest.TestCase):
    C = dict(atr_multiplier=2.0, risk_fraction=0.005, max_positions=3,
             fee=0.001, slippage=0.0005, initial_cash=10000.0,
             breakout_bars=20, support_bars=10)

    def _rows(self, n, step, offset=0.5):
        out, price = [], 1000.0
        for i in range(n):
            price += step
            out.append(dict(t=i * FOUR_HOUR, o=price, h=price + offset, l=price - offset,
                            c=price, v=100.0))
        return out

    def _write(self, data_dir, symbols_rows, start, end):
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "manifest.json").write_text(json.dumps({
            "symbols": list(symbols_rows), "timeframe": "4h", "start": start, "end": end,
        }), encoding="utf-8")
        for symbol, rows in symbols_rows.items():
            (data_dir / f"{symbol}.json").write_text(json.dumps(rows), encoding="utf-8")

    def test_downtrend_publishes_a_short_adayi_event_and_pending_short(self):
        n = 260
        rows = self._rows(n, -2.0)
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            data_dir, runtime = tmp / "data", tmp / "runtime"
            self._write(data_dir, {"BTCUSDT": rows, "ALTUSDT": rows}, 0, n * FOUR_HOUR)
            write_short_state(self.C, data_dir, runtime, now=n * FOUR_HOUR)
            saved = json.loads((runtime / "short_state.json").read_text(encoding="utf-8"))
        events = saved["state"]["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "SHORT_ADAYI")
        self.assertEqual(events[0]["symbol"], "ALTUSDT")
        self.assertIn("ALTUSDT", saved["state"]["pending_shorts"])
        self.assertIn("leverage", saved["state"]["pending_shorts"]["ALTUSDT"])

    def test_flat_data_publishes_nothing(self):
        n = 260
        rows = [dict(t=i * FOUR_HOUR, o=100.0, h=100.5, l=99.5, c=100.0, v=100.0) for i in range(n)]
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            data_dir, runtime = tmp / "data", tmp / "runtime"
            self._write(data_dir, {"BTCUSDT": rows, "ALTUSDT": rows}, 0, n * FOUR_HOUR)
            write_short_state(self.C, data_dir, runtime, now=n * FOUR_HOUR)
            saved = json.loads((runtime / "short_state.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["state"]["events"], [])
        self.assertEqual(saved["state"]["pending_shorts"], {})


class WriteTwoHourSignalStateTests(unittest.TestCase):
    """write_2h_signal_state (18 Sep 2026) is the 2H system's own
    independent fetch+detect+publish pass, deliberately separate from
    prepare_data/write_short_state's 4H path -- these tests exercise it
    end to end (fetch mocked, detection/publishing real) since it had no
    coverage at all when first written."""

    C = dict(atr_multiplier=2.0, risk_fraction=0.005, max_positions=3,
             fee=0.001, slippage=0.0005, initial_cash=10000.0,
             breakout_bars=20, support_bars=10)

    def _rows(self, n, step, offset=0.5):
        out, price = [], 1000.0
        for i in range(n):
            price += step
            out.append(dict(t=i * TWO_HOUR, o=price, h=price + offset, l=price - offset,
                            c=price, v=100.0))
        return out

    def _run(self, rows, now_2h):
        with patch('crypto_v1.github_worker.universe', return_value=['ALTUSDT']), \
             patch('crypto_v1.github_worker.candles', side_effect=lambda symbol, start, end, interval: rows), \
             patch('crypto_v1.github_worker.validate', side_effect=lambda rows, interval: rows):
            with tempfile.TemporaryDirectory() as tmp:
                runtime = Path(tmp)
                write_2h_signal_state(self.C, runtime, now_2h=now_2h)
                long_saved = json.loads((runtime / "state_2h.json").read_text(encoding="utf-8"))
                short_saved = json.loads((runtime / "short_state_2h.json").read_text(encoding="utf-8"))
        return long_saved, short_saved

    def test_uptrend_publishes_an_al_adayi_event_and_pending_buy(self):
        n = 260
        long_saved, short_saved = self._run(self._rows(n, 2.0), now_2h=n * TWO_HOUR)
        events = long_saved["state"]["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "AL_ADAYI")
        self.assertEqual(events[0]["symbol"], "ALTUSDT")
        self.assertIn("ALTUSDT", long_saved["state"]["pending_buys"])
        self.assertEqual(short_saved["state"]["events"], [])

    def test_downtrend_publishes_a_short_adayi_event_not_a_long_one(self):
        n = 260
        long_saved, short_saved = self._run(self._rows(n, -2.0), now_2h=n * TWO_HOUR)
        self.assertEqual(long_saved["state"]["events"], [])
        events = short_saved["state"]["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "SHORT_ADAYI")
        self.assertEqual(events[0]["symbol"], "ALTUSDT")
        self.assertIn("ALTUSDT", short_saved["state"]["pending_shorts"])
        self.assertIn("leverage", short_saved["state"]["pending_shorts"]["ALTUSDT"])

    def test_flat_data_publishes_nothing_on_either_file(self):
        n = 260
        rows = [dict(t=i * TWO_HOUR, o=100.0, h=100.5, l=99.5, c=100.0, v=100.0) for i in range(n)]
        long_saved, short_saved = self._run(rows, now_2h=n * TWO_HOUR)
        self.assertEqual(long_saved["state"]["events"], [])
        self.assertEqual(short_saved["state"]["events"], [])

    def test_fetch_window_is_at_least_200_bars_for_ema200_to_populate(self):
        now = 1_800_000_000_000
        with patch('crypto_v1.github_worker.universe', return_value=['ALTUSDT']), \
             patch('crypto_v1.github_worker.candles', return_value=[]) as mock_candles, \
             patch('crypto_v1.github_worker.validate', return_value=[]):
            with tempfile.TemporaryDirectory() as tmp:
                write_2h_signal_state(self.C, Path(tmp), now_2h=now)
        start = mock_candles.call_args_list[0].args[1]
        self.assertGreaterEqual((now - start) / TWO_HOUR, 200)
