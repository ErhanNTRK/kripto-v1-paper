import io
import json
import unittest
import urllib.error
from unittest.mock import Mock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption

from crypto_v1.binance_futures import FuturesExecutor, OrderRejected, OrderStateUnknown
from crypto_v1.live_execution import LIVE_PHRASE


def _http_error(code, msg="rejected"):
    body = json.dumps({"code": code, "msg": msg}).encode()
    return urllib.error.HTTPError("https://fapi.binance.com", 400, "bad", {}, io.BytesIO(body))


class Response:
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self): return json.dumps(self.body).encode()


class BinanceFuturesTests(unittest.TestCase):
    def setUp(self):
        key = Ed25519PrivateKey.generate()
        self.pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode()
        self.env = {"LIVE_TRADING_CONFIRMATION": LIVE_PHRASE, "BINANCE_API_KEY": "api",
                    "BINANCE_ED25519_PRIVATE_KEY": self.pem}

    def test_disabled_executor_never_contacts_network(self):
        opener = Mock()
        executor = FuturesExecutor({"live_trading_enabled": False}, {}, opener=opener)
        with self.assertRaisesRegex(RuntimeError, "disabled"):
            executor.market_open_short("BTCUSDT", "0.01", "kv1fs-1")
        opener.assert_not_called()

    def test_disabled_executor_can_only_query(self):
        opener = Mock(return_value=Response([]))
        env = {"BINANCE_API_KEY": "api", "BINANCE_ED25519_PRIVATE_KEY": self.pem}
        self.assertEqual(FuturesExecutor({"live_trading_enabled": False}, env, opener).open_orders(), [])
        self.assertEqual(opener.call_count, 1)

    def test_market_open_short_is_signed_and_reduceonly_is_absent_on_open(self):
        opener = Mock(return_value=Response({"status": "FILLED"}))
        result = FuturesExecutor({"live_trading_enabled": True}, self.env, opener).market_open_short(
            "BTCUSDT", "0.01", "kv1fs-1")
        self.assertEqual(result["status"], "FILLED")
        body = opener.call_args.args[0].data.decode()
        self.assertIn("side=SELL", body)
        self.assertIn("newClientOrderId=kv1fs-1", body)
        self.assertNotIn("reduceOnly", body)
        self.assertIn("signature=", body)

    def test_protective_stop_for_short_is_a_reduceonly_buy_stop_market(self):
        opener = Mock(return_value=Response({"status": "NEW"}))
        FuturesExecutor({"live_trading_enabled": True}, self.env, opener).protective_stop_for_short(
            "BTCUSDT", "0.01", "26000", "kv1fp-1")
        body = opener.call_args.args[0].data.decode()
        self.assertIn("side=BUY", body)
        self.assertIn("type=STOP_MARKET", body)
        self.assertIn("reduceOnly=true", body)

    def test_market_close_short_is_a_reduceonly_buy_market(self):
        opener = Mock(return_value=Response({"status": "FILLED"}))
        FuturesExecutor({"live_trading_enabled": True}, self.env, opener).market_close_short(
            "BTCUSDT", "0.01", "kv1fx-1")
        body = opener.call_args.args[0].data.decode()
        self.assertIn("side=BUY", body)
        self.assertIn("reduceOnly=true", body)

    def test_set_isolated_margin_tolerates_already_set(self):
        opener = Mock(side_effect=_http_error(-4046, "No need to change margin type."))
        result = FuturesExecutor({"live_trading_enabled": True}, self.env, opener).set_isolated_margin("BTCUSDT")
        self.assertTrue(result["already_set"])

    def test_set_isolated_margin_reraises_other_rejections(self):
        opener = Mock(side_effect=_http_error(-1000))
        with self.assertRaises(OrderRejected):
            FuturesExecutor({"live_trading_enabled": True}, self.env, opener).set_isolated_margin("BTCUSDT")

    def test_non_allowlisted_endpoint_is_rejected(self):
        opener = Mock()
        with self.assertRaises(ValueError):
            FuturesExecutor({"live_trading_enabled": True}, self.env, opener).request(
                "POST", {}, path="/fapi/v1/allOpenOrders")
        opener.assert_not_called()

    def test_ambiguous_post_must_not_be_retried(self):
        opener = Mock(side_effect=TimeoutError())
        with self.assertRaises(OrderStateUnknown):
            FuturesExecutor({"live_trading_enabled": True}, self.env, opener).market_open_short(
                "BTCUSDT", "0.01", "kv1fs-1")

    def test_http_rejection_has_code_but_no_secrets(self):
        opener = Mock(side_effect=_http_error(-2010, "account has insufficient balance"))
        with self.assertRaises(OrderRejected) as ctx:
            FuturesExecutor({"live_trading_enabled": True}, self.env, opener).market_open_short(
                "BTCUSDT", "0.01", "kv1fs-1")
        self.assertEqual(ctx.exception.code, -2010)
        self.assertNotIn(self.pem, str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
