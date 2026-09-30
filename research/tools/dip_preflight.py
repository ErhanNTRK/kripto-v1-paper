"""Pre-flight for the live dip-catcher: real account (GET only), real Binance rules, real prices; every order the
code WOULD send is only printed. Nothing is posted."""
import json, sys, tempfile, urllib.request
from decimal import Decimal
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # the checkout this file lives in
from crypto_v1.binance_futures import signed_futures_request
from crypto_v1.dip_live import LiveDipCatcher
from crypto_v1.live_execution import futures_symbol_rules

S = Path(r"C:\Users\ASUS-PC\kripto\secrets")
KEY, PEM = (S / "BINANCE_API_KEY").read_text().strip(), (S / "BINANCE_ED25519_PRIVATE_KEY").read_text()
get = lambda path, p: signed_futures_request("GET", path, p, KEY, PEM)
INFO = {e["symbol"]: e for e in json.load(urllib.request.urlopen("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=20))["symbols"]}


class ReadOnlyExecutor:
    def account(self):
        return get("/fapi/v2/account", {})

    def open_orders(self):
        plain = get("/fapi/v1/openOrders", {})
        algo = [{**o, "clientOrderId": o.get("clientAlgoId", "")} for o in get("/fapi/v1/openAlgoOrders", {})]
        return plain + algo

    def __getattr__(self, name):          # any order-sending method: print only
        return lambda *a, **k: print("GONDERILMEZDI:", name, a) or {"status": "NEW", "executedQty": "0"}


class Market:
    def rules(self, symbol):
        return futures_symbol_rules(INFO[symbol])


acct = ReadOnlyExecutor().account()
sleeve = Decimal(acct["totalMarginBalance"]) * Decimal("0.7")
cfg = json.loads(Path(r"C:\Users\ASUS-PC\Documents\GitHub\kripto-v1-paper\short_live_config_2h.json").read_text(encoding="utf-8"))["dip_catcher"]
cfg = dict(cfg, mode="live")
state = Path(tempfile.mkdtemp()) / "dip.json"
dip = LiveDipCatcher(cfg, state, Path(r"C:\Users\ASUS-PC\kripto\src\runtime\manifest.json"), ReadOnlyExecutor(), Market(),
                     send=lambda m: print("TELEGRAM:", m), equity_fn=lambda: sleeve)
print(f"hesap {acct['totalMarginBalance']} USDT, 2H kasasi ~{sleeve:.2f}, kullanilabilir {acct['availableBalance']}")
dip.tick()
