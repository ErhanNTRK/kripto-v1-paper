"""READ-ONLY account snapshot: futures wallet, every open position with its resting orders (plain + algo), spot total.
GET requests only, never prints secrets. Run from anywhere:  PYTHONIOENCODING=utf-8 ~/kripto/venv/Scripts/python.exe research/tools/hesap_durumu.py"""
import sys, json
from pathlib import Path
sys.path.insert(0, r"C:\Users\ASUS-PC\kripto\src")
from crypto_v1.binance_futures import signed_futures_request
from crypto_v1.binance_account import signed_get
import urllib.request
S = Path(r"C:\Users\ASUS-PC\kripto\secrets")
key, pem = (S / "BINANCE_API_KEY").read_text().strip(), (S / "BINANCE_ED25519_PRIVATE_KEY").read_text()
get = lambda path, p: signed_futures_request("GET", path, p, key, pem)
for p in get("/fapi/v2/positionRisk", {}):
    if float(p["positionAmt"]) != 0:
        print({k: p.get(k) for k in ("symbol", "positionAmt", "entryPrice", "markPrice", "unRealizedProfit", "liquidationPrice", "leverage", "notional")})
        for path in ("/fapi/v1/openOrders", "/fapi/v1/openAlgoOrders"):
            try:
                for o in get(path, {"symbol": p["symbol"]}):
                    print("   ", path.split("/")[-1], {k: o.get(k) for k in ("type", "orderType", "side", "price", "stopPrice", "triggerPrice", "origQty", "quantity", "closePosition", "reduceOnly") if o.get(k) not in (None, "")})
            except Exception as e:
                print("   ", path, "okunamadi:", e)
a = signed_futures_request("GET", "/fapi/v2/account", {}, key, pem)
print("VADELI: cuzdan", a["totalWalletBalance"], "| gerceklesmemis", a["totalUnrealizedProfit"], "| toplam", a["totalMarginBalance"])
for p in a["positions"]:
    if float(p["positionAmt"]): print("   ", p["symbol"].encode("unicode_escape").decode(), p["positionAmt"], "gerceklesmemis", p["unrealizedProfit"])
spot = signed_get("/api/v3/account", key, pem)
prices = {x["symbol"]: float(x["price"]) for x in json.load(urllib.request.urlopen("https://api.binance.com/api/v3/ticker/price"))}
tot = 0
for b in spot["balances"]:
    q = float(b["free"]) + float(b["locked"])
    if q <= 0: continue
    usd = q if b["asset"] in ("USDT", "USDC", "FDUSD") else q * prices.get(b["asset"] + "USDT", 0)
    tot += usd
    if usd > 0.5: print("   SPOT", b["asset"], q, f"~{usd:.2f} USDT")
print(f"SPOT toplam ~{tot:.2f} USDT")
