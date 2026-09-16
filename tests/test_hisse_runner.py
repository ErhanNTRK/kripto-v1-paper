import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from hisse import runner


class Response:
    def __init__(self, payload):
        self.payload = payload
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self):
        return json.dumps(self.payload).encode()


class ResolveChatIdTests(unittest.TestCase):
    def test_uses_cached_file_without_any_network_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            (runtime / 'telegram_chat.json').write_text(json.dumps({'chat_id': '999'}))
            with patch('urllib.request.urlopen') as mock_open:
                chat_id = runner.resolve_chat_id(runtime)
            mock_open.assert_not_called()
        self.assertEqual(chat_id, '999')

    def test_falls_back_to_the_crypto_pilots_published_chat_id_and_caches_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            with patch('urllib.request.urlopen', return_value=Response({'chat_id': '1505732236'})):
                chat_id = runner.resolve_chat_id(runtime)
            self.assertEqual(chat_id, '1505732236')
            cached = json.loads((runtime / 'telegram_chat.json').read_text())
            self.assertEqual(cached['chat_id'], '1505732236')


if __name__ == '__main__':
    unittest.main()
