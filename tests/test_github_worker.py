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
             patch('crypto_v1.github_worker.futures_tradable_symbols', return_value={'ETHUSDT', 'SOLUSDT'}), \
             patch('crypto_v1.github_worker.candles', return_value=[]), \
             patch('crypto_v1.github_worker.validate', return_value=[]):
            prepare_data({}, runtime, now=0)
        return mock_universe, json.loads((runtime / 'manifest.json').read_text(encoding='utf-8'))

    def test_a_cached_manifest_is_filtered_to_futures_tradable_symbols(self):
        # 22 Sep 2026: universe() started filtering to Futures-tradable
        # symbols, but a cached 4h manifest never calls universe() again,
        # so the 4H detectors kept scanning Spot-only symbols (FETUSDT-
        # style) that every live entry attempt rejects with -1121.
        cached = {'symbols': ['SOLUSDT', 'FETUSDT'], 'timeframe': '4h', 'source': 'x', 'ranked_at': 0}
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            (runtime / 'manifest.json').write_text(json.dumps(cached), encoding='utf-8')
            with patch('crypto_v1.github_worker.universe') as mock_universe, \
                 patch('crypto_v1.github_worker.futures_tradable_symbols', return_value={'SOLUSDT'}), \
                 patch('crypto_v1.github_worker.candles', return_value=[]) as mock_candles, \
                 patch('crypto_v1.github_worker.validate', return_value=[]):
                data_dir = prepare_data({}, runtime, now=0)
            persisted = json.loads((runtime / 'manifest.json').read_text(encoding='utf-8'))
            run_manifest = json.loads((data_dir / 'manifest.json').read_text(encoding='utf-8'))
        mock_universe.assert_not_called()
        self.assertEqual(persisted['symbols'], ['SOLUSDT'])
        self.assertEqual(run_manifest['symbols'], ['SOLUSDT'])
        fetched = {c.args[0] for c in mock_candles.call_args_list}
        self.assertEqual(fetched, {'SOLUSDT', 'BTCUSDT'})

    def test_a_failed_tradable_lookup_leaves_the_cached_manifest_alone(self):
        cached = {'symbols': ['SOLUSDT', 'FETUSDT'], 'timeframe': '4h', 'source': 'x', 'ranked_at': 0}
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            (runtime / 'manifest.json').write_text(json.dumps(cached), encoding='utf-8')
            with patch('crypto_v1.github_worker.universe'), \
                 patch('crypto_v1.github_worker.futures_tradable_symbols', return_value=set()), \
                 patch('crypto_v1.github_worker.candles', return_value=[]), \
                 patch('crypto_v1.github_worker.validate', return_value=[]):
                prepare_data({}, runtime, now=0)
            persisted = json.loads((runtime / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(persisted['symbols'], ['SOLUSDT', 'FETUSDT'])

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
        cached = {'symbols': ['SOLUSDT'], 'timeframe': '4h', 'source': 'x', 'ranked_at': 0}
        with tempfile.TemporaryDirectory() as tmp:
            mock_universe, manifest = self._run(Path(tmp), existing_manifest=cached)
        mock_universe.assert_not_called()
        self.assertEqual(manifest['symbols'], ['SOLUSDT'])

    def test_a_day_old_manifest_is_re_ranked(self):
        # 23 Sep 2026: the 4H list had been frozen since 22 Sep, so a coin
        # new to the top 50 (ACEUSDT) was never scanned by the 4H side.
        cached = {'symbols': ['SOLUSDT'], 'timeframe': '4h', 'source': 'x', 'ranked_at': 0}
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            (runtime / 'manifest.json').write_text(json.dumps(cached), encoding='utf-8')
            with patch('crypto_v1.github_worker.universe', return_value=['ETHUSDT']) as mock_universe,                  patch('crypto_v1.github_worker.futures_tradable_symbols', return_value={'ETHUSDT'}),                  patch('crypto_v1.github_worker.candles', return_value=[]),                  patch('crypto_v1.github_worker.validate', return_value=[]):
                prepare_data({}, runtime, now=86_400_000)
            persisted = json.loads((runtime / 'manifest.json').read_text(encoding='utf-8'))
        mock_universe.assert_called_once()
        self.assertEqual(persisted['symbols'], ['ETHUSDT'])
        self.assertEqual(persisted['ranked_at'], 86_400_000)

    def test_a_manifest_from_before_daily_ranking_is_refreshed(self):
        legacy = {'symbols': ['SOLUSDT'], 'timeframe': '4h', 'source': 'x'}
        with tempfile.TemporaryDirectory() as tmp:
            mock_universe, manifest = self._run(Path(tmp), existing_manifest=legacy)
        mock_universe.assert_called_once()
        self.assertEqual(manifest['symbols'], ['ETHUSDT'])

    def test_fetch_window_is_at_least_200_bars_for_ema200_to_ever_populate(self):
        # Regression: a 30-day (180-bar) window meant indicators.features'
        # ema200 (needs >=200 closes) never populated, so btc_up_ok/
        # btc_down_ok were always False and no entry signal could ever
        # fire -- caught only by zero trades/events in the live paper
        # state despite real market moves (18 Sep 2026), not by a test.
        now = 1_800_000_000_000
        with patch('crypto_v1.github_worker.universe', return_value=['ETHUSDT']), \
             patch('crypto_v1.github_worker.futures_tradable_symbols', return_value={'ETHUSDT'}), \
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
                 patch('crypto_v1.github_worker._print_4h_long_scan'), \
                 patch('crypto_v1.github_worker.write_short_state') as mock_short, \
                 patch('crypto_v1.github_worker.write_2h_signal_state') as mock_2h:
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
        # All candidate/fill Telegram notifications from this shadow paper
        # state were dropped (21 Sep 2026): AL_ADAYI/SHORT_ADAYI told the
        # user nothing actionable, and this state's own AL/SAT events are
        # a closed-candle SIMULATION that never places a real order --
        # format_event gave no visual hint of that, so it looked identical
        # to a real fill and confused the user into expecting a real buy.

    def test_prints_the_4h_long_scan_count_after_tick(self):
        # Live-reported 22 Sep 2026: this path (the paper-engine-driven
        # AL_ADAYI feed used for real 4H long entries) printed nothing at
        # all, so right after startup the user saw only the SHORT_ADAYI
        # line and concluded the system was "only searching for short, and
        # only 4H" -- it was actually running, just invisible.
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            data_dir = runtime / 'data'
            data_dir.mkdir()
            (data_dir / 'manifest.json').write_text(
                json.dumps({'symbols': ['ETHUSDT', 'SOLUSDT']}), encoding='utf-8')
            state_path = runtime / 'state-relaxed.json'
            now = 800 * FOUR_HOUR

            def fake_tick(*args, **kwargs):
                state_path.write_text(json.dumps({'state': {'events': [
                    {'type': 'AL_ADAYI', 'time': now, 'symbol': 'ETHUSDT'},
                    {'type': 'AL_ADAYI', 'time': now - FOUR_HOUR, 'symbol': 'SOLUSDT'},  # stale bar, not counted
                ]}}), encoding='utf-8')

            with patch('crypto_v1.github_worker.get', return_value={'serverTime': now}), \
                 patch('crypto_v1.github_worker.prepare_data', return_value=data_dir), \
                 patch('crypto_v1.github_worker.tick', side_effect=fake_tick), \
                 patch('crypto_v1.github_worker.write_short_state'), \
                 patch('crypto_v1.github_worker.write_2h_signal_state'), \
                 patch('builtins.print') as mock_print:
                local_tick(runtime)
        printed = [c.args[0] for c in mock_print.call_args_list]
        self.assertIn('4H long tarama: 2 sembol kontrol edildi, 1 AL_ADAYI bulundu.', printed)

    def test_retries_once_on_new_paper_state_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            (runtime / 'state-relaxed.json').write_text('{}', encoding='utf-8')
            with patch('crypto_v1.github_worker.get', return_value={'serverTime': 800 * FOUR_HOUR}), \
                 patch('crypto_v1.github_worker.prepare_data', return_value=runtime / 'data'), \
                 patch('crypto_v1.github_worker.tick',
                      side_effect=[ValueError('new paper state required'), None]) as mock_tick, \
                 patch('crypto_v1.github_worker._print_4h_long_scan'), \
                 patch('crypto_v1.github_worker.write_short_state'), \
                 patch('crypto_v1.github_worker.write_2h_signal_state'):
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

    def test_symbol_dropping_out_of_the_reranked_universe_keeps_its_candidate(self):
        # Bug found live 21 Sep 2026: universe() re-ranks by 24h volume
        # fresh on every local_tick call (every 5 minutes once live, not
        # GitHub Actions' old rare cron), and a symbol can fall out of
        # top_n between two calls still evaluating the SAME candle
        # (now_2h unchanged) in a fast-moving market. Before
        # _write_candidate_state's merge, the second call's full overwrite
        # silently erased the first call's already-Telegram-announced,
        # still-fresh AL_ADAYI candidate before auto_enter ever saw it.
        n = 260
        rows = self._rows(n, 2.0)
        now_2h = n * TWO_HOUR
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            with patch('crypto_v1.github_worker.universe', return_value=['ALTUSDT', 'BETAUSDT']), \
                 patch('crypto_v1.github_worker.candles', side_effect=lambda symbol, start, end, interval: rows), \
                 patch('crypto_v1.github_worker.validate', side_effect=lambda rows, interval: rows):
                write_2h_signal_state(self.C, runtime, now_2h=now_2h)
            first = json.loads((runtime / "state_2h.json").read_text(encoding="utf-8"))
            self.assertIn("ALTUSDT", first["state"]["pending_buys"])
            self.assertIn("BETAUSDT", first["state"]["pending_buys"])
            # Same window, but ALTUSDT fell out of the freshly re-ranked
            # top_n this time -- it must still be in the merged output.
            with patch('crypto_v1.github_worker.universe', return_value=['BETAUSDT']), \
                 patch('crypto_v1.github_worker.candles', side_effect=lambda symbol, start, end, interval: rows), \
                 patch('crypto_v1.github_worker.validate', side_effect=lambda rows, interval: rows):
                write_2h_signal_state(self.C, runtime, now_2h=now_2h)
            second = json.loads((runtime / "state_2h.json").read_text(encoding="utf-8"))
        self.assertIn("ALTUSDT", second["state"]["pending_buys"])
        self.assertIn("BETAUSDT", second["state"]["pending_buys"])

    def test_a_new_window_still_fully_replaces_old_candidates(self):
        n = 260
        rows = self._rows(n, 2.0)
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            with patch('crypto_v1.github_worker.universe', return_value=['ALTUSDT']), \
                 patch('crypto_v1.github_worker.candles', side_effect=lambda symbol, start, end, interval: rows), \
                 patch('crypto_v1.github_worker.validate', side_effect=lambda rows, interval: rows):
                write_2h_signal_state(self.C, runtime, now_2h=n * TWO_HOUR)
            with patch('crypto_v1.github_worker.universe', return_value=['BETAUSDT']), \
                 patch('crypto_v1.github_worker.candles', side_effect=lambda symbol, start, end, interval: rows), \
                 patch('crypto_v1.github_worker.validate', side_effect=lambda rows, interval: rows):
                write_2h_signal_state(self.C, runtime, now_2h=(n + 1) * TWO_HOUR)
            saved = json.loads((runtime / "state_2h.json").read_text(encoding="utf-8"))
        self.assertNotIn("ALTUSDT", saved["state"]["pending_buys"])
        self.assertIn("BETAUSDT", saved["state"]["pending_buys"])


class LocalTickWindowGateTests(unittest.TestCase):
    """22 Sep 2026: signals only use closed candles, so each 4H/2H window is
    detected once; re-running it every loop cost minutes of fetching and
    left too few entry attempts inside the 30-minute window."""

    def _patches(self, server_time, fail_2h=False):
        return (patch('crypto_v1.github_worker.get', return_value={'serverTime': server_time}),
                patch('crypto_v1.github_worker.prepare_data'),
                patch('crypto_v1.github_worker.tick'),
                patch('crypto_v1.github_worker._print_4h_long_scan'),
                patch('crypto_v1.github_worker.write_short_state'),
                patch('crypto_v1.github_worker.write_2h_signal_state',
                      side_effect=RuntimeError('network') if fail_2h else None))

    def _call(self, runtime, server_time, fail_2h=False):
        p = self._patches(server_time, fail_2h)
        with p[0], p[1] as prep, p[2], p[3], p[4], p[5] as two:
            try:
                local_tick(runtime)
            except RuntimeError:
                pass
        return prep.call_count, two.call_count

    def test_same_window_is_detected_only_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = 800 * FOUR_HOUR
            self.assertEqual(self._call(Path(tmp), now), (1, 1))
            self.assertEqual(self._call(Path(tmp), now + 60_000), (0, 0))

    def test_new_2h_window_reruns_only_the_2h_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = 800 * FOUR_HOUR
            self._call(Path(tmp), now)
            self.assertEqual(self._call(Path(tmp), now + TWO_HOUR), (0, 1))

    def test_a_failed_pass_is_retried_on_the_next_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = 800 * FOUR_HOUR
            self._call(Path(tmp), now, fail_2h=True)
            self.assertEqual(self._call(Path(tmp), now), (0, 1))


class BtcHistoryStartTests(unittest.TestCase):
    """With the regime filter on, BTC must be fetched far enough back for
    its 200-day line; the filter fails closed, so a 45-day fetch would have
    silently blocked every long."""

    def test_regime_filter_extends_only_btc(self):
        from crypto_v1.github_worker import btc_history_start
        now, start = 1_000 * 86_400_000, 955 * 86_400_000
        self.assertEqual(btc_history_start({}, now, start), start)
        self.assertEqual(btc_history_start({"regime_ma_days": 200}, now, start), now - 220 * 86_400_000)

    def test_fetch_all_uses_the_longer_start_for_btc_only(self):
        from crypto_v1.github_worker import fetch_all
        seen = {}
        def fake(symbol, start, end, interval):
            seen[symbol] = start
            return []
        with patch('crypto_v1.github_worker.candles', side_effect=fake),              patch('crypto_v1.github_worker.validate', side_effect=lambda rows, interval: rows):
            fetch_all(["ETHUSDT", "BTCUSDT"], 500, 1000, FOUR_HOUR, btc_start=100)
        self.assertEqual(seen, {"ETHUSDT": 500, "BTCUSDT": 100})


class TwoHourStrategyFileTests(unittest.TestCase):
    """config_v5_long_2h.json must stay the 4H strategy with its BAR counts
    doubled (2h bars are half as long); drifting apart silently is how the
    2H system ended up measuring half the calendar time."""

    def test_2h_file_is_the_4h_file_with_bar_counts_doubled(self):
        four = json.loads(Path("config_v5_long.json").read_text(encoding="utf-8"))
        two = json.loads(Path("config_v5_long_2h.json").read_text(encoding="utf-8"))
        self.assertEqual(two["donchian_windows"], [2 * w for w in four.get("donchian_windows", [10, 20, 40])])
        self.assertEqual(two["first_time_high_bars"], 2 * four["first_time_high_bars"])
        shared = {k for k in four if k not in ("donchian_windows", "first_time_high_bars", "trailing_atr",
                                                "max_week_gain")}
        self.assertEqual({k: two[k] for k in shared}, {k: four[k] for k in shared})

    def test_short_sides_follow_the_24_sep_2026_decision(self):
        # 3-year test, shorts alone: 2H 50 -> 23 USDT, 4H 50 -> 45. The user
        # switched 2H shorts off and kept 4H shorts only while BTC is under
        # its 200-day average (where the long side is closed anyway).
        four = json.loads(Path("config_v5_long.json").read_text(encoding="utf-8"))
        two = json.loads(Path("config_v5_long_2h.json").read_text(encoding="utf-8"))
        self.assertTrue(two.get("disable_shorts"))
        self.assertFalse(four.get("disable_shorts"))
        self.assertTrue(four.get("short_regime_only"))
        self.assertEqual(four.get("regime_ma_days"), 200)

    def test_the_week_gain_cap_is_4h_only(self):
        # 4H: 184 -> 205 USDT over 3 years with a 50% cap; 2H: 477 -> 459.
        four = json.loads(Path("config_v5_long.json").read_text(encoding="utf-8"))
        two = json.loads(Path("config_v5_long_2h.json").read_text(encoding="utf-8"))
        self.assertEqual(four.get("max_week_gain"), 0.5)
        self.assertNotIn("max_week_gain", two)

    def test_the_btc_exit_delay_is_2h_only(self):
        # 2H: 1766 -> 3376 over 3 years; on 4H the same delay changed nothing.
        four = json.loads(Path("config_v5_long.json").read_text(encoding="utf-8"))
        two = json.loads(Path("config_v5_long_2h.json").read_text(encoding="utf-8"))
        self.assertEqual(two.get("btc_exit_bars"), 2)
        self.assertNotIn("btc_exit_bars", four)

    def test_both_systems_use_the_ichimoku_filter(self):
        for name in ("config_v5_long.json", "config_v5_long_2h.json"):
            self.assertTrue(json.loads(Path(name).read_text(encoding="utf-8")).get("ichimoku_filter"), name)

