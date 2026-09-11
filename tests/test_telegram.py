import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from crypto_v1.telegram import deliver_once, format_event, send_message


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

    def test_message_is_explicitly_simulated(self):
        message = format_event(dict(type='AL', symbol='BTCUSDT', time=0, price=100))
        self.assertIn('SANAL ISLEM', message)
        self.assertIn('Gercek emir verilmedi', message)

    def test_network_errors_do_not_expose_token(self):
        with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN':'test-secret', 'TELEGRAM_CHAT_ID':'123'}), patch('urllib.request.urlopen', side_effect=ValueError('test-secret')):
            with self.assertRaises(RuntimeError) as exc:
                send_message('test')
            self.assertNotIn('test-secret', str(exc.exception))
