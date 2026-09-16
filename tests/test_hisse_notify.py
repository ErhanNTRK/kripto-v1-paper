import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from hisse import notify

DAY = 86400


class DedupeKeyTests(unittest.TestCase):
    def test_once_scope_uses_dedupe_id_not_the_run_time(self):
        event = dict(kind='post_ex_dividend', dedupe_scope='once', dedupe_id=123, message='x')
        k1 = notify.dedupe_key('KO', event, now_s=0)
        k2 = notify.dedupe_key('KO', event, now_s=999 * DAY)
        self.assertEqual(k1, k2)

    def test_day_scope_differs_across_days_but_not_within_a_day(self):
        event = dict(kind='last_buy_date', dedupe_scope='day', message='x')
        k_morning = notify.dedupe_key('KO', event, now_s=1 * DAY + 3600)
        k_evening = notify.dedupe_key('KO', event, now_s=1 * DAY + 3600 * 20)
        k_next_day = notify.dedupe_key('KO', event, now_s=2 * DAY + 3600)
        self.assertEqual(k_morning, k_evening)
        self.assertNotEqual(k_morning, k_next_day)

    def test_week_scope_is_stable_within_the_week(self):
        event = dict(kind='high_yield', dedupe_scope='week', message='x')
        k1 = notify.dedupe_key('KO', event, now_s=1 * DAY)
        k2 = notify.dedupe_key('KO', event, now_s=2 * DAY)
        self.assertEqual(k1, k2)

    def test_different_symbols_never_collide(self):
        event = dict(kind='high_yield', dedupe_scope='week', message='x')
        self.assertNotEqual(notify.dedupe_key('KO', event, 0), notify.dedupe_key('PEP', event, 0))


class DeliverScanResultsTests(unittest.TestCase):
    def test_sends_each_event_with_the_hisse_prefix(self):
        results = {'KO': [dict(kind='high_yield', dedupe_scope='week', message='msg')]}
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'telegram.sqlite'
            with patch('hisse.notify.deliver_once', wraps=lambda key, msg, database: True) as mock_deliver:
                sent = notify.deliver_scan_results(results, now_s=0, database=db)
        self.assertEqual(sent, 1)
        mock_deliver.assert_called_once()
        self.assertTrue(mock_deliver.call_args[0][1].startswith('HISSE | '))


class NotifyErrorsTests(unittest.TestCase):
    def test_no_call_when_no_errors(self):
        with patch('hisse.notify.deliver_once') as mock_deliver:
            sent = notify.notify_errors({}, database='x', run_id='r1')
        self.assertFalse(sent)
        mock_deliver.assert_not_called()

    def test_summarizes_errors_into_one_message(self):
        errors = {'A': 'boom', 'B': 'timeout'}
        with patch('hisse.notify.deliver_once', return_value=True) as mock_deliver:
            notify.notify_errors(errors, database='x', run_id='r1')
        text = mock_deliver.call_args[0][1]
        self.assertIn('A: boom', text)
        self.assertIn('B: timeout', text)


if __name__ == '__main__':
    unittest.main()
