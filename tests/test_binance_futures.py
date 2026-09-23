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
        # Plain resting orders AND the algo service's (our protective stops).
        self.assertEqual(opener.call_count, 2)
        self.assertIn("/fapi/v1/openAlgoOrders", opener.call_args.args[0].full_url)

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
        # Conditional orders live in the Algo service since 2025-12-09:
        # /fapi/v1/algoOrder, triggerPrice and clientAlgoId. /fapi/v1/order
        # answers STOP_MARKET with -4120, which is how every stop silently
        # failed on 22 Sep 2026.
        opener = Mock(return_value=Response({"algoStatus": "NEW", "clientAlgoId": "kv1fp-1"}))
        result = FuturesExecutor({"live_trading_enabled": True}, self.env, opener).protective_stop_for_short(
            "BTCUSDT", "0.01", "26000", "kv1fp-1")
        request = opener.call_args.args[0]
        body = request.data.decode()
        self.assertEqual(request.full_url, "https://fapi.binance.com/fapi/v1/algoOrder")
        self.assertIn("algoType=CONDITIONAL", body)
        self.assertIn("side=BUY", body)
        self.assertIn("type=STOP_MARKET", body)
        self.assertIn("triggerPrice=26000", body)
        self.assertIn("clientAlgoId=kv1fp-1", body)
        self.assertIn("reduceOnly=true", body)
        # Normalized into the plain-order shape the rest of the code reads.
        self.assertEqual(result["status"], "NEW")
        self.assertEqual(result["clientOrderId"], "kv1fp-1")

    def test_protective_stop_for_long_sells_through_the_algo_endpoint(self):
        opener = Mock(return_value=Response({"algoStatus": "NEW"}))
        FuturesExecutor({"live_trading_enabled": True}, self.env, opener).protective_stop_for_long(
            "BTCUSDT", "0.01", "24000", "kv1fq-1")
        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, "https://fapi.binance.com/fapi/v1/algoOrder")
        self.assertIn("side=SELL", request.data.decode())

    def test_query_and_cancel_route_stops_to_the_algo_endpoint(self):
        opener = Mock(return_value=Response({"algoStatus": "CANCELLED", "clientAlgoId": "kv1fq-1",
                                             "triggerPrice": "24000", "quantity": "0.01"}))
        executor = FuturesExecutor({"live_trading_enabled": True}, self.env, opener)
        canceled = executor.cancel("BTCUSDT", "kv1fq-1")
        self.assertIn("/fapi/v1/algoOrder", opener.call_args.args[0].full_url)
        self.assertIn("clientAlgoId=kv1fq-1", opener.call_args.args[0].data.decode())
        # The exit monitors accept only CANCELED/EXPIRED/FILLED.
        self.assertEqual(canceled["status"], "CANCELED")
        self.assertEqual(canceled["stopPrice"], "24000")
        queried = executor.query("BTCUSDT", "kv1fq-1")
        self.assertIn("/fapi/v1/algoOrder", opener.call_args.args[0].full_url)
        self.assertEqual(queried["origQty"], "0.01")

    def test_plain_orders_still_use_the_normal_endpoint(self):
        opener = Mock(return_value=Response({"status": "FILLED"}))
        FuturesExecutor({"live_trading_enabled": True}, self.env, opener).query("BTCUSDT", "kv1fl-1")
        self.assertIn("/fapi/v1/order", opener.call_args.args[0].full_url)
        self.assertNotIn("algo", opener.call_args.args[0].full_url)

    def test_a_triggered_stop_reads_as_filled(self):
        from crypto_v1.binance_futures import normalize_algo_order
        self.assertEqual(normalize_algo_order({"algoStatus": "TRIGGERED"})["status"], "FILLED")
        self.assertEqual(normalize_algo_order({"algoStatus": "EXPIRED"})["status"], "EXPIRED")

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
