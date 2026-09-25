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

from .data import note_slow
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
    # Conditional orders (our protective stops) moved to the Algo service
    # on 2025-12-09: /fapi/v1/order now rejects STOP_MARKET with -4120
    # "Please use the Algo Order API endpoints instead". Live consequence
    # (22 Sep 2026): every entry filled and then failed to place its stop,
    # leaving real positions unprotected.
    ("POST", "/fapi/v1/algoOrder"),
    ("GET", "/fapi/v1/algoOrder"),
    ("DELETE", "/fapi/v1/algoOrder"),
    ("GET", "/fapi/v1/openAlgoOrders"),
    # Read-only history for the trade ledger (crypto_v1.ledger).
    ("GET", "/fapi/v1/userTrades"),
    ("GET", "/fapi/v1/income"),
    ("POST", "/fapi/v1/leverage"),
    ("POST", "/fapi/v1/marginType"),
    ("GET", "/fapi/v2/account"),
    ("GET", "/fapi/v2/positionRisk"),
    ("GET", "/fapi/v1/exchangeInfo"),
}

# Binance rejects a signed request whose timestamp is older than this when
# it arrives (-1021). 5 s was too tight for the PC's link: live 23 Sep 2026
# a single slow request (clock was only ~40 ms off) aborted a whole futures
# exit scan. 10 s still refuses a genuinely stale, replayed request.
RECV_WINDOW_MS = 10000


def signed_futures_request(method, path, params, api_key, private_pem, clock=None,
                           opener=urllib.request.urlopen):
    method = method.upper()
    if (method, path) not in ALLOWED:
        raise ValueError("futures endpoint is not allow-listed")
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
            raise OrderStateUnknown("Binance Futures order result is unknown; query by client order ID") from None
        raise RuntimeError("Binance Futures order query failed") from None


# Our protective-stop client ids (short kv1fp, long kv1fq). Everything
# else we send is a plain MARKET order on /fapi/v1/order.
ALGO_STOP_PREFIXES = ("kv1fp", "kv1fq")

_ALGO_STATUS = {"NEW": "NEW", "TRIGGERED": "FILLED", "FILLED": "FILLED", "FINISHED": "FILLED",
                "CANCELLED": "CANCELED", "CANCELED": "CANCELED", "EXPIRED": "EXPIRED"}


def normalize_algo_order(order, default_status="UNKNOWN"):
    """Present an algo order in the plain-order shape the rest of the code
    already reads (clientOrderId/stopPrice/origQty/status), so live_positions,
    summarize_short_pilot and the exit monitors work unchanged.

    A state not in the table (TRIGGERING, REJECTED, a new one) is passed on
    as itself, never read as NEW: until 24 Sep 2026 anything unknown became
    NEW, so a dead stop could pass for a resting one. Only the open-orders
    list defaults to NEW -- everything in it is open by definition."""
    status = str(order.get("algoStatus", "")).upper()
    return {**order,
            "clientOrderId": order.get("clientAlgoId", ""),
            "stopPrice": order.get("triggerPrice", "0"),
            "origQty": order.get("quantity", "0"),
            "type": order.get("orderType", "STOP_MARKET"),
            "status": _ALGO_STATUS.get(status, status or default_status)}


class FuturesExecutor:
    CANCEL_SETTLE_TRIES = 4
    CANCEL_SETTLE_WAIT = 1.0

    def __init__(self, config, environment, opener=urllib.request.urlopen, sleep=time.sleep):
        self.config = config
        self.environment = environment
        self.opener = opener
        self.sleep = sleep

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
        try:
            return signed_futures_request(method, path, params, api_key, private_key,
                                          opener=self.opener)
        except OrderRejected as error:
            # -1021: the request arrived after its recvWindow. Binance
            # rejected it before acting on it -- nothing was read or placed
            # -- so one retry with a fresh timestamp is safe for every method.
            if error.code != -1021:
                raise
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

    def _algo_stop(self, symbol, side, quantity, stop_price, client_id):
        # Algo service (see ALLOWED): conditional orders take triggerPrice
        # and clientAlgoId where /fapi/v1/order took stopPrice and
        # newClientOrderId. reduceOnly keeps it a pure position-closer.
        return normalize_algo_order(self.request(
            "POST", {"algoType": "CONDITIONAL", "symbol": symbol, "side": side,
                     "type": "STOP_MARKET", "quantity": quantity,
                     "triggerPrice": stop_price, "reduceOnly": "true",
                     "clientAlgoId": client_id},
            path="/fapi/v1/algoOrder"))

    def protective_stop_for_short(self, symbol, quantity, stop_price, client_id):
        # Fires (buys back) if price rises to stop_price, closing the short.
        return self._algo_stop(symbol, "BUY", quantity, stop_price, client_id)

    def protective_stop_for_long(self, symbol, quantity, stop_price, client_id):
        return self._algo_stop(symbol, "SELL", quantity, stop_price, client_id)

    def market_close_short(self, symbol, quantity, client_id):
        return self.request("POST", {"symbol": symbol, "side": "BUY", "type": "MARKET",
                                     "quantity": quantity, "reduceOnly": "true",
                                     "newClientOrderId": client_id, "newOrderRespType": "FULL"})

    def market_close_long(self, symbol, quantity, client_id):
        return self.request("POST", {"symbol": symbol, "side": "SELL", "type": "MARKET",
                                     "quantity": quantity, "reduceOnly": "true",
                                     "newClientOrderId": client_id, "newOrderRespType": "FULL"})

    def query(self, symbol, client_id):
        # A protective stop lives in the Algo service; everything else is a
        # plain order. Routing by client-id prefix keeps _known_or_place and
        # the exit monitors identical for both.
        if str(client_id).startswith(ALGO_STOP_PREFIXES):
            return normalize_algo_order(
                self.request("GET", {"clientAlgoId": client_id}, path="/fapi/v1/algoOrder"))
        return self.request("GET", {"symbol": symbol, "origClientOrderId": client_id})

    def cancel(self, symbol, client_id):
        if not str(client_id).startswith(ALGO_STOP_PREFIXES):
            return self.request("DELETE", {"symbol": symbol, "origClientOrderId": client_id})
        try:
            canceled = normalize_algo_order(
                self.request("DELETE", {"clientAlgoId": client_id}, path="/fapi/v1/algoOrder"))
        except OrderRejected as error:
            # -2011: nothing left to cancel -- an earlier attempt already did,
            # or it triggered. Live 24 Sep 2026 every tick hit this on a stop
            # cancelled the tick before and aborted the whole exit scan.
            if error.code != -2011:
                raise
            canceled = {"clientOrderId": client_id, "status": "NEW"}
        if canceled["status"] in {"CANCELED", "EXPIRED", "FILLED"}:
            return canceled
        # The delete response does not always carry a final algoStatus, and
        # the exit monitors refuse to close a position whose stop is not
        # provably gone. Live 23 Sep 2026: the stop WAS cancelled, this read
        # as unconfirmed, the exit aborted -- and the position was left both
        # open and unprotected. Ask what actually happened before deciding;
        # the query itself can lag the delete by a moment (24 Sep 2026), so
        # it is asked a few times.
        for attempt in range(self.CANCEL_SETTLE_TRIES):
            if attempt:
                self.sleep(self.CANCEL_SETTLE_WAIT)
            try:
                settled = self.query(symbol, client_id)
            except OrderRejected as error:
                if error.code != -2013:  # Binance: order does not exist -> it is gone.
                    raise
                return {**canceled, "status": "CANCELED"}
            if settled["status"] in {"CANCELED", "EXPIRED", "FILLED"}:
                break
        return settled

    def open_orders(self):
        """Plain resting orders plus the algo service's own -- our protective
        stops now live there, and held_symbols/committed/live_positions all
        read this one list."""
        plain = self.request("GET", {}, path="/fapi/v1/openOrders")
        algo = self.request("GET", {}, path="/fapi/v1/openAlgoOrders")
        return plain + [normalize_algo_order(o, default_status="NEW") for o in algo]

    def all_orders(self, symbol, start_time=None):
        params = {"symbol": symbol, "limit": 1000}
        if start_time is not None:
            params["startTime"] = int(start_time)
        return self.request("GET", params, path="/fapi/v1/allOrders")

    def user_trades(self, symbol, start_time, end_time):
        """Fills for one symbol; Binance serves at most a 7-day window."""
        return self.request("GET", {"symbol": symbol, "startTime": int(start_time), "endTime": int(end_time),
                                    "limit": 1000}, path="/fapi/v1/userTrades")

    def income(self, start_time, end_time, income_type=None):
        """Realized P&L, commission, funding and transfer events; one kind
        only when income_type (e.g. "TRANSFER") is given."""
        params = {"startTime": int(start_time), "endTime": int(end_time), "limit": 1000}
        if income_type:
            params["incomeType"] = income_type
        return self.request("GET", params, path="/fapi/v1/income")

    def account(self):
        return self.request("GET", {}, path="/fapi/v2/account")

    def position_risk(self, symbol=None):
        params = {"symbol": symbol} if symbol else {}
        return self.request("GET", params, path="/fapi/v2/positionRisk")
