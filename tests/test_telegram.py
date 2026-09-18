import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from crypto_v1.telegram import (deliver_once, deliver_short_events, format_daily_status,
                                format_event, send_message)


class TelegramTests(unittest.TestCase):
    def test_replay_sends_once(self):
        with tempfile.TemporaryDirectory() as folder, patch('crypto_v1.telegram.send_message', return_value=123) as send:
            db = Path(folder)/'delivery.sqlite'
            self.assertTrue(deliver_once('event1', 'test', db))
            self.assertFalse(deliver_once('event1', 'test', db))
            self.assertEqual(send.call_count, 1)

    def test_uncertain_delivery_is_not_automatically_repeated(self):
        with tempfile.TemporaryDirectory() as folder, patch('crypto_v1.telegram.send_message', side_effect=RuntimeError('unconfirmed')) as send:
            db = Path(folder)/'delivery.sqlite'
            with self.assertRaises(RuntimeError):
                deliver_once('event1', 'test', db)
            self.assertFalse(deliver_once('event1', 'test', db))
            self.assertEqual(send.call_count, 1)

    def test_missing_configuration_never_contacts_network(self):
        with patch.dict(os.environ, {}, clear=True), patch('urllib.request.urlopen') as request:
            with self.assertRaises(RuntimeError):
                send_message('test')
            request.assert_not_called()

    def test_message_identifies_itself_as_a_signal(self):
        message = format_event(dict(type='AL', symbol='BTCUSDT', time=0, price=100))
        self.assertIn('SINYAL', message)

    def test_al_adayi_no_longer_asks_for_a_manual_al_reply(self):
        # Regression (18 Sep 2026): with live trading on, this must no
        # longer claim "SANAL ISLEM / Gercek emir verilmedi" (virtual
        # trade, no real order given) -- AL_ADAYI is a real candidate.
        # Then entries became fully automatic (render_web.LiveApp.
        # auto_enter): telling the user to reply "AL yaz" was actively
        # misleading once the position opened on its own regardless of
        # any reply, reported 18 Sep 2026 as "AL dedim ama almadi".
        message = format_event(dict(type='AL_ADAYI', symbol='BTCUSDT', time=0, close=100))
        self.assertNotIn('SANAL ISLEM', message)
        self.assertNotIn('Gercek emir verilmedi', message)
        self.assertNotIn('AL yaz', message)
        self.assertIn('otomatik', message)

    def test_short_adayi_also_says_it_is_automatic(self):
        message = format_event(dict(type='SHORT_ADAYI', symbol='BTCUSDT', time=0, close=100))
        self.assertNotIn('AL yaz', message)
        self.assertIn('otomatik', message)

    def test_daily_status_reports_halt_and_trade_count(self):
        state = {'cash': 68.13, 'positions': {}, 'halted': True,
                 'trades': [{'exit_t': 1000}]}
        message = format_daily_status(state, 1000)
        self.assertIn('GUNLUK ZARAR KESICI AKTIF', message)
        self.assertIn('kapanan sanal islem: 1', message)
        self.assertIn('68.13 USDT', message)

    def test_network_errors_do_not_expose_token(self):
        with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN':'test-secret', 'TELEGRAM_CHAT_ID':'123'}), patch('urllib.request.urlopen', side_effect=ValueError('test-secret')):
            with self.assertRaises(RuntimeError) as exc:
                send_message('test')
            self.assertNotIn('test-secret', str(exc.exception))

    def test_deliver_short_events_sends_each_short_adayi_once(self):
        saved = {'state': {'events': [
            {'type': 'SHORT_ADAYI', 'time': 1000, 'symbol': 'SOLUSDT', 'close': 100},
        ], 'pending_shorts': {'SOLUSDT': {'stop': 105, 'leverage': 3}}}}
        with tempfile.TemporaryDirectory() as folder, \
             patch('crypto_v1.telegram.send_message', return_value=1) as send, \
             patch('crypto_v1.telegram.time.sleep'):
            path = Path(folder) / 'short_state.json'
            path.write_text(json.dumps(saved), encoding='utf-8')
            db = Path(folder) / 'delivery.sqlite'
            sent = deliver_short_events(path, db)
            self.assertEqual(sent, 1)
            # Re-running against the same (unchanged) file must not resend.
            self.assertEqual(deliver_short_events(path, db), 0)
            self.assertEqual(send.call_count, 1)

    def test_deliver_short_events_ignores_non_short_adayi_events(self):
        saved = {'state': {'events': [{'type': 'AL_ADAYI', 'time': 1000, 'symbol': 'SOLUSDT', 'close': 100}],
                           'pending_shorts': {}}}
        with tempfile.TemporaryDirectory() as folder, patch('crypto_v1.telegram.send_message') as send:
            path = Path(folder) / 'short_state.json'
            path.write_text(json.dumps(saved), encoding='utf-8')
            db = Path(folder) / 'delivery.sqlite'
            self.assertEqual(deliver_short_events(path, db), 0)
            send.assert_not_called()
