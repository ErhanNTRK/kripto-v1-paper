"""Minimal read-only Binance Spot account client using Ed25519 signatures."""
import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from cryptography.hazmat.primitives.serialization import load_pem_private_key
BASE = "https://api.binance.com"

# Binance rejects a signed request whose timestamp is older than this when
# it arrives (-1021). 5 s was too tight for the PC's link: live 23 Sep 2026
# a single slow request (clock was only ~40 ms off) aborted a whole futures
# exit scan. 10 s still refuses a genuinely stale, replayed request.
RECV_WINDOW_MS = 10000

def signed_get(path, api_key, private_pem, params=None, clock=None):
    if path not in ("/api/v3/account", "/sapi/v1/account/apiRestrictions"):
        raise ValueError("Read-only account endpoint required")
    values = dict(params or {})
    values["timestamp"] = int((clock or time.time)() * 1000)
    values["recvWindow"] = RECV_WINDOW_MS
    payload = urllib.parse.urlencode(values)
    key = load_pem_private_key(private_pem.encode("utf-8"), password=None)
    values["signature"] = base64.b64encode(key.sign(payload.encode("ascii"))).decode("ascii")
    url = BASE + path + "?" + urllib.parse.urlencode(values)
    request = urllib.request.Request(url, headers={"X-MBX-APIKEY": api_key})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        try:
            detail = json.loads(error.read().decode("utf-8"))
            code = int(detail.get("code", 0))
            message = str(detail.get("msg", "rejected"))[:160]
        except Exception:
            code, message = error.code, "rejected"
        raise RuntimeError(f"Binance rejected signed request: {code} {message}") from None
    except Exception:
        raise RuntimeError("Binance signed account request failed") from None

def verify_from_environment():
    api_key = os.environ.get("BINANCE_API_KEY", "")
    private_key = os.environ.get("BINANCE_ED25519_PRIVATE_KEY", "")
    if not api_key or "BEGIN PRIVATE KEY" not in private_key:
        raise RuntimeError("Binance credentials are missing")
    account = signed_get("/api/v3/account", api_key, private_key)
    restrictions = signed_get("/sapi/v1/account/apiRestrictions", api_key, private_key)
    if not account.get("canTrade"):
        raise RuntimeError("Binance Spot trading permission is unavailable")
    if restrictions.get("enableWithdrawals"):
        raise RuntimeError("Withdrawal permission must be disabled")
    usdt = next((float(x["free"]) for x in account.get("balances", []) if x["asset"] == "USDT"), 0.0)
    return {"connected": True, "can_trade": True, "withdrawals": False, "free_usdt": usdt}
