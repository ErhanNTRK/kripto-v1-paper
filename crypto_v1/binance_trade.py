"""Gated Binance Spot order transport using Ed25519 signatures."""
import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from cryptography.hazmat.primitives.serialization import load_pem_private_key

from .live_execution import execution_enabled

BASE = "https://api.binance.com"
ALLOWED = {
    ("POST", "/api/v3/order"),
    ("GET", "/api/v3/order"),
    ("DELETE", "/api/v3/order"),
}


class OrderRejected(RuntimeError):
    pass


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
    values["recvWindow"] = 5000
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
    try:
        with opener(request, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        try:
            detail = json.loads(error.read().decode("utf-8"))
            code = int(detail.get("code", error.code))
        except Exception:
            code = error.code
        raise OrderRejected(f"Binance rejected order request: {code}") from None
    except Exception:
        if method in {"POST", "DELETE"}:
            raise OrderStateUnknown("Binance order result is unknown; query by client order ID") from None
        raise RuntimeError("Binance order query failed") from None


class SpotExecutor:
    def __init__(self, config, environment, opener=urllib.request.urlopen):
        self.config = config
        self.environment = environment
        self.opener = opener

    def _credentials(self):
        if not execution_enabled(self.config, self.environment):
            raise RuntimeError("real order execution is disabled")
        api_key = self.environment.get("BINANCE_API_KEY", "")
        private_key = self.environment.get("BINANCE_ED25519_PRIVATE_KEY", "")
        if not api_key or "BEGIN PRIVATE KEY" not in private_key:
            raise RuntimeError("Binance credentials are missing")
        return api_key, private_key

    def request(self, method, params):
        api_key, private_key = self._credentials()
        return signed_order_request(method, "/api/v3/order", params, api_key, private_key,
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
