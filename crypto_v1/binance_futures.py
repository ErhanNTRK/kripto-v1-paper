"""Gated Binance USD-M Futures order transport using Ed25519 signatures.

Mirrors binance_trade.py's SpotExecutor exactly (same signing scheme,
same allow-list-or-reject design, same error taxonomy) but targets the
Futures API (fapi.binance.com) for leveraged short/long positions -- the
short side of the long+short roadmap needs real leverage and liquidation
handling that Spot cannot provide."""
import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from cryptography.hazmat.primitives.serialization import load_pem_private_key

from .binance_trade import OrderRejected, OrderStateUnknown
from .live_execution import execution_enabled

BASE = "https://fapi.binance.com"
ALLOWED = {
    ("POST", "/fapi/v1/order"),
    ("GET", "/fapi/v1/order"),
    ("DELETE", "/fapi/v1/order"),
    ("GET", "/fapi/v1/openOrders"),
    ("GET", "/fapi/v1/allOrders"),
    ("POST", "/fapi/v1/leverage"),
    ("POST", "/fapi/v1/marginType"),
    ("GET", "/fapi/v2/account"),
    ("GET", "/fapi/v2/positionRisk"),
    ("GET", "/fapi/v1/exchangeInfo"),
}


def signed_futures_request(method, path, params, api_key, private_pem, clock=None,
                           opener=urllib.request.urlopen):
    method = method.upper()
    if (method, path) not in ALLOWED:
        raise ValueError("futures endpoint is not allow-listed")
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
        raise OrderRejected(code) from None
    except Exception:
        if method in {"POST", "DELETE"}:
            raise OrderStateUnknown("Binance Futures order result is unknown; query by client order ID") from None
        raise RuntimeError("Binance Futures order query failed") from None


class FuturesExecutor:
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

    def request(self, method, params, path="/fapi/v1/order"):
        api_key, private_key = self._credentials(require_live=method.upper() != "GET")
        return signed_futures_request(method, path, params, api_key, private_key,
                                      opener=self.opener)

    def set_leverage(self, symbol, leverage):
        return self.request("POST", {"symbol": symbol, "leverage": int(leverage)},
                            path="/fapi/v1/leverage")

    def set_isolated_margin(self, symbol):
        try:
            return self.request("POST", {"symbol": symbol, "marginType": "ISOLATED"},
                                path="/fapi/v1/marginType")
        except OrderRejected as error:
            if error.code == -4046:  # Binance: "No need to change margin type."
                return {"marginType": "ISOLATED", "already_set": True}
            raise

    def market_open_short(self, symbol, quantity, client_id):
        return self.request("POST", {"symbol": symbol, "side": "SELL", "type": "MARKET",
                                     "quantity": quantity, "newClientOrderId": client_id,
                                     "newOrderRespType": "FULL"})

    def market_open_long(self, symbol, quantity, client_id):
        return self.request("POST", {"symbol": symbol, "side": "BUY", "type": "MARKET",
                                     "quantity": quantity, "newClientOrderId": client_id,
                                     "newOrderRespType": "FULL"})

    def protective_stop_for_short(self, symbol, quantity, stop_price, client_id):
        # STOP_MARKET BUY, reduceOnly: fires (buys back) if price rises to
        # stop_price, closing the short. No limit leg -- Futures stop-market
        # fills at the best available price once triggered.
        return self.request("POST", {"symbol": symbol, "side": "BUY", "type": "STOP_MARKET",
                                     "quantity": quantity, "stopPrice": stop_price,
                                     "reduceOnly": "true", "newClientOrderId": client_id})

    def protective_stop_for_long(self, symbol, quantity, stop_price, client_id):
        return self.request("POST", {"symbol": symbol, "side": "SELL", "type": "STOP_MARKET",
                                     "quantity": quantity, "stopPrice": stop_price,
                                     "reduceOnly": "true", "newClientOrderId": client_id})

    def market_close_short(self, symbol, quantity, client_id):
        return self.request("POST", {"symbol": symbol, "side": "BUY", "type": "MARKET",
                                     "quantity": quantity, "reduceOnly": "true",
                                     "newClientOrderId": client_id, "newOrderRespType": "FULL"})

    def market_close_long(self, symbol, quantity, client_id):
        return self.request("POST", {"symbol": symbol, "side": "SELL", "type": "MARKET",
                                     "quantity": quantity, "reduceOnly": "true",
                                     "newClientOrderId": client_id, "newOrderRespType": "FULL"})

    def query(self, symbol, client_id):
        return self.request("GET", {"symbol": symbol, "origClientOrderId": client_id})

    def cancel(self, symbol, client_id):
        return self.request("DELETE", {"symbol": symbol, "origClientOrderId": client_id})

    def open_orders(self):
        return self.request("GET", {}, path="/fapi/v1/openOrders")

    def all_orders(self, symbol, start_time=None):
        params = {"symbol": symbol, "limit": 1000}
        if start_time is not None:
            params["startTime"] = int(start_time)
        return self.request("GET", params, path="/fapi/v1/allOrders")

    def account(self):
        return self.request("GET", {}, path="/fapi/v2/account")

    def position_risk(self, symbol=None):
        params = {"symbol": symbol} if symbol else {}
        return self.request("GET", params, path="/fapi/v2/positionRisk")
