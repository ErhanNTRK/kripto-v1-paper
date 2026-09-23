"""Gated Binance Spot order transport using Ed25519 signatures."""
import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .data import note_slow
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from .live_execution import execution_enabled

BASE = "https://api.binance.com"
ALLOWED = {
    ("POST", "/api/v3/order"),
    ("GET", "/api/v3/order"),
    ("DELETE", "/api/v3/order"),
    ("GET", "/api/v3/openOrders"),
    ("GET", "/api/v3/allOrders"),
}

# Binance rejects a signed request whose timestamp is older than this when
# it arrives (-1021). 5 s was too tight for the PC's link: live 23 Sep 2026
# a single slow request (clock was only ~40 ms off) aborted a whole futures
# exit scan. 10 s still refuses a genuinely stale, replayed request.
RECV_WINDOW_MS = 10000


class OrderRejected(RuntimeError):
    """`message` is Binance's own "msg" text and `http_status` the HTTP code.
    Both matter for -1003: HTTP 429 is a per-minute weight limit, HTTP 418
    is an IP ban whose msg says exactly how long ("...banned until
    <epoch ms>"), and every request sent during that ban extends it."""

    def __init__(self, code, message="", http_status=None):
        self.code = int(code)
        self.message = message or ""
        self.http_status = http_status
        # str() stays code-only on purpose: exception text ends up in logs
        # and Binance's msg is untrusted (see test_http_rejection_has_code_
        # but_no_secrets). Callers that want the text read .message.
        super().__init__(f"Binance rejected order request: {self.code}")


class OrderStateUnknown(RuntimeError):
    """The request may have reached Binance; callers must query before retrying."""
    pass


def signed_order_request(method, path, params, api_key, private_pem, clock=None,
                         opener=urllib.request.urlopen):
    method = method.upper()
    if (method, path) not in ALLOWED:
        raise ValueError("order endpoint is not allow-listed")
    values = dict(params)
    values["timestamp"] = int((clock or time.time)() * 1000)
    values["recvWindow"] = RECV_WINDOW_MS
    payload = urllib.parse.urlencode(values)
    key = load_pem_private_key(private_pem.encode("utf-8"), password=None)
    values["signature"] = base64.b64encode(key.sign(payload.encode("ascii"))).decode("ascii")
    encoded = urllib.parse.urlencode(values).encode("ascii")
    url = BASE + path
    if method == "GET":
        request = urllib.request.Request(url + "?" + encoded.decode("ascii"),
                                         headers={"X-MBX-APIKEY": api_key}, method="GET")
    else:
        request = urllib.request.Request(url, data=encoded,
                                         headers={"X-MBX-APIKEY": api_key,
                                                  "Content-Type": "application/x-www-form-urlencoded"},
                                         method=method)
    started = time.time()
    try:
        with opener(request, timeout=20) as response:
            result = json.load(response)
        note_slow(f"{method} {path}", started)
        return result
    except urllib.error.HTTPError as error:
        note_slow(f"{method} {path}", started, f"HTTP {error.code}")
        message = ""
        try:
            detail = json.loads(error.read().decode("utf-8"))
            code = int(detail.get("code", error.code))
            message = str(detail.get("msg", ""))
        except Exception:
            code = error.code
        raise OrderRejected(code, message, error.code) from None
    except Exception as exc:
        note_slow(f"{method} {path}", started, type(exc).__name__)
        if method in {"POST", "DELETE"}:
            raise OrderStateUnknown("Binance order result is unknown; query by client order ID") from None
        raise RuntimeError("Binance order query failed") from None


class SpotExecutor:
    def __init__(self, config, environment, opener=urllib.request.urlopen):
        self.config = config
        self.environment = environment
        self.opener = opener

    def _credentials(self, require_live=True):
        if require_live and not execution_enabled(self.config, self.environment):
            raise RuntimeError("real order execution is disabled")
        api_key = self.environment.get("BINANCE_API_KEY", "")
        private_key = self.environment.get("BINANCE_ED25519_PRIVATE_KEY", "")
        if not api_key or "BEGIN PRIVATE KEY" not in private_key:
            raise RuntimeError("Binance credentials are missing")
        return api_key, private_key

    def request(self, method, params, path="/api/v3/order"):
        api_key, private_key = self._credentials(require_live=method.upper() != "GET")
        return signed_order_request(method, path, params, api_key, private_key,
                                    opener=self.opener)

    def market_buy(self, symbol, quote_amount, client_id):
        return self.request("POST", {"symbol": symbol, "side": "BUY", "type": "MARKET",
                                     "quoteOrderQty": quote_amount,
                                     "newClientOrderId": client_id,
                                     "newOrderRespType": "FULL"})

    def protective_stop(self, symbol, quantity, stop_price, limit_price, client_id):
        return self.request("POST", {"symbol": symbol, "side": "SELL",
                                     "type": "STOP_LOSS_LIMIT", "timeInForce": "GTC",
                                     "quantity": quantity, "stopPrice": stop_price,
                                     "price": limit_price, "newClientOrderId": client_id})

    def market_sell(self, symbol, quantity, client_id):
        return self.request("POST", {"symbol": symbol, "side": "SELL", "type": "MARKET",
                                     "quantity": quantity, "newClientOrderId": client_id,
                                     "newOrderRespType": "FULL"})

    def query(self, symbol, client_id):
        return self.request("GET", {"symbol": symbol, "origClientOrderId": client_id})

    def cancel(self, symbol, client_id):
        return self.request("DELETE", {"symbol": symbol, "origClientOrderId": client_id})

    def open_orders(self):
        return self.request("GET", {}, path="/api/v3/openOrders")

    def all_orders(self, symbol, start_time=None):
        params = {"symbol": symbol, "limit": 1000}
        if start_time is not None:
            params["startTime"] = int(start_time)
        return self.request("GET", params, path="/api/v3/allOrders")
