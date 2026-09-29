"""READ-ONLY: GET requests only (orders history, positions, open orders). Never prints secrets."""
import json, sys, time
from pathlib import Path
sys.path.insert(0, r"C:\Users\ASUS-PC\kripto\src")
from crypto_v1.binance_futures import signed_futures_request
S = Path(r"C:\Users\ASUS-PC\kripto\secrets")
key, pem = (S / "BINANCE_API_KEY").read_text().strip(), (S / "BINANCE_ED25519_PRIVATE_KEY").read_text()
get = lambda path, p: signed_futures_request("GET", path, p, key, pem)
t0 = int((time.time() - 6 * 3600) * 1000)
print("=== LITEUSDT emirleri (son 6 saat) ===")
for o in get("/fapi/v1/allOrders", {"symbol": "LITEUSDT", "startTime": t0}):
    print({k: o.get(k) for k in ("orderId", "clientOrderId", "type", "origType", "side", "status", "price", "avgPrice",
                                  "stopPrice", "executedQty", "reduceOnly", "closePosition")},
          time.strftime("%H:%M:%S", time.localtime(o["time"] / 1000)), "->", time.strftime("%H:%M:%S", time.localtime(o["updateTime"] / 1000)))
for sym in ("LITEUSDT", "BNBUSDT"):
    try:
        algos = get("/fapi/v1/openAlgoOrders", {"symbol": sym})
    except Exception as e:
        algos = f"hata {e}"
    print(f"\n{sym} acik algo/kosullu emirler:", json.dumps(algos, ensure_ascii=False)[:1500])
    print(f"{sym} acik normal emirler:", json.dumps(get("/fapi/v1/openOrders", {"symbol": sym}), ensure_ascii=False)[:1200])
print("\n=== BNBUSDT emirleri (son 6 saat) ===")
for o in get("/fapi/v1/allOrders", {"symbol": "BNBUSDT", "startTime": t0}):
    print({k: o.get(k) for k in ("clientOrderId", "type", "origType", "side", "status", "price", "avgPrice", "stopPrice", "executedQty", "reduceOnly")},
          time.strftime("%H:%M:%S", time.localtime(o["time"] / 1000)))
print("\n=== acik pozisyonlar ===")
for p in get("/fapi/v2/positionRisk", {}):
    if float(p["positionAmt"]) != 0:
        print({k: p.get(k) for k in ("symbol", "positionAmt", "entryPrice", "markPrice", "unRealizedProfit", "liquidationPrice", "leverage", "marginType")})
