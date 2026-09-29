"""Real entry slippage: bot market BUY avgPrice vs the futures price at the moment the signal bar closed
(open of the 1m bar at the time embedded in the clientOrderId). Also exits (reduceOnly SELL)."""
import json, re, urllib.request, statistics as S, time
orders = json.load(open(r"C:\Users\ASUS-PC\kripto\src\runtime\bot_orders.json"))
def open_at(sym, t):
    u = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1m&startTime={t}&limit=1"
    k = json.load(urllib.request.urlopen(u, timeout=10))
    return float(k[0][1]) if k else None
rows = []
for o in orders:
    if o.get("status") != "FILLED" or o.get("type") != "MARKET":
        continue
    m = re.search(r"(1\d{12})", o["clientOrderId"])
    if not m:
        continue
    t = int(m.group(1)); ref = open_at(o["symbol"], t); time.sleep(0.05)
    if not ref:
        continue
    px = float(o["avgPrice"])
    side = o["side"]; ro = o.get("reduceOnly")
    bps = (px / ref - 1) * 1e4 * (1 if side == "BUY" else -1)   # positive = worse for us
    rows.append((o["clientOrderId"][:5], side, ro, o["symbol"], round(bps, 1), round((o["time"] - t) / 1000)))
ent = [r for r in rows if r[1] == "BUY" and not r[2] and 0 <= r[5] < 900]
import collections; print("gecikme dagilimi", sorted(r[5] for r in ent))
ext = []
for name, rs in (("GIRIS", ent), ("CIKIS (reduceOnly)", ext)):
    b = [r[4] for r in rs]
    if b:
        print(f"{name}: n={len(b)} medyan {S.median(b):+.1f} bp, ort {S.mean(b):+.1f} bp, en kotu {max(b):+.1f} bp, gecikme medyan {S.median([r[5] for r in rs])} sn")
print("prefixler:", sorted({r[0] for r in rows}))
print("en kotu 5 giris:", sorted(ent, key=lambda r: -r[4])[:5])
