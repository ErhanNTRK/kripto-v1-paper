import io
import json
import unittest
import urllib.error
from unittest.mock import Mock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption

from crypto_v1.binance_trade import (OrderRejected, OrderStateUnknown, SpotExecutor,
                                     signed_order_request)
from crypto_v1.live_execution import LIVE_PHRASE


class Response:
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self): return json.dumps(self.body).encode()


class BinanceTradeTests(unittest.TestCase):
    def setUp(self):
        key = Ed25519PrivateKey.generate()
        self.pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode()

    def test_disabled_executor_never_contacts_network(self):
        opener = Mock()
        executor = SpotExecutor({"live_trading_enabled": False}, {}, opener=opener)
        with self.assertRaisesRegex(RuntimeError, "disabled"):
            executor.market_buy("BTCUSDT", "10", "kv1-buy-1")
        opener.assert_not_called()

    def test_market_buy_is_signed_and_has_client_id(self):
        opener = Mock(return_value=Response({"status": "FILLED"}))
        env = {"LIVE_TRADING_CONFIRMATION": LIVE_PHRASE, "BINANCE_API_KEY": "api",
               "BINANCE_ED25519_PRIVATE_KEY": self.pem}
        result = SpotExecutor({"live_trading_enabled": True}, env, opener).market_buy(
            "BTCUSDT", "10", "kv1-buy-1")
        self.assertEqual(result["status"], "FILLED")
        request = opener.call_args.args[0]
        body = request.data.decode()
        self.assertIn("newClientOrderId=kv1-buy-1", body)
        self.assertIn("signature=", body)

    def test_ambiguous_post_must_not_be_retried(self):
        with self.assertRaises(OrderStateUnknown):
            signed_order_request("POST", "/api/v3/order", {"symbol": "BTCUSDT"},
                                 "api", self.pem, opener=Mock(side_effect=TimeoutError()))

    def test_http_rejection_has_code_but_no_secrets(self):
        error = urllib.error.HTTPError("https://example", 400, "bad", {},
                                      io.BytesIO(b'{"code":-1013,"msg":"bad api-secret"}'))
        with self.assertRaises(OrderRejected) as caught:
            signed_order_request("POST", "/api/v3/order", {"symbol": "BTCUSDT"},
                                 "api-secret", self.pem, opener=Mock(side_effect=error))
        self.assertIn("-1013", str(caught.exception))
        self.assertNotIn("api-secret", str(caught.exception))

    def test_non_allowlisted_endpoint_is_rejected(self):
        with self.assertRaises(ValueError):
            signed_order_request("POST", "/sapi/v1/capital/withdraw/apply", {},
                                 "api", self.pem)
