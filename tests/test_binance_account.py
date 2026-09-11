import unittest
from unittest.mock import patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
from crypto_v1.binance_account import signed_get

class Response:
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self): return b'{"canTrade":true}'

class BinanceAccountTests(unittest.TestCase):
    def setUp(self):
        key = Ed25519PrivateKey.generate()
        self.pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode()
    def test_only_read_endpoints_are_allowed(self):
        with self.assertRaises(ValueError): signed_get('/api/v3/order', 'key', self.pem)
    def test_signed_account_request(self):
        with patch('urllib.request.urlopen', return_value=Response()) as call:
            self.assertTrue(signed_get('/api/v3/account', 'abc', self.pem, clock=lambda: 1)['canTrade'])
            self.assertIn('signature=', call.call_args.args[0].full_url)
    def test_errors_do_not_expose_credentials(self):
        with patch('urllib.request.urlopen', side_effect=ValueError('secret')):
            with self.assertRaises(RuntimeError) as error: signed_get('/api/v3/account', 'api-secret', self.pem)
            self.assertNotIn('api-secret', str(error.exception))
