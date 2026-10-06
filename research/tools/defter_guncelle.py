"""READ-ONLY trade-ledger refresh into a separate folder (default ~/kripto/islem-kayitlari-okuma): the bot's own ledger code fed
by a GET-only client. Usage: python research/tools/defter_guncelle.py [folder]; then read <folder>/islemler.csv and ozet.txt.
The first run copies binance-kayitlari.json from the bot's ledger so it only fetches what is new."""
import sys
from pathlib import Path
sys.path.insert(0, r"C:\Users\ASUS-PC\kripto\src")
from crypto_v1.binance_futures import signed_futures_request
from crypto_v1 import ledger
S = Path(r"C:\Users\ASUS-PC\kripto\secrets")
key, pem = (S / "BINANCE_API_KEY").read_text().strip(), (S / "BINANCE_ED25519_PRIVATE_KEY").read_text()
class ReadOnly:
    def _get(self, path, params): return signed_futures_request("GET", path, params, key, pem)
    def income(self, start, end, income_type=None):
        p = {"startTime": int(start), "endTime": int(end), "limit": 1000}
        if income_type: p["incomeType"] = income_type
        return self._get("/fapi/v1/income", p)
    def user_trades(self, symbol, start, end):
        return self._get("/fapi/v1/userTrades", {"symbol": symbol, "startTime": int(start), "endTime": int(end), "limit": 1000})
    def all_orders(self, symbol, start_time=None):
        p = {"symbol": symbol, "limit": 1000}
        if start_time is not None: p["startTime"] = int(start_time)
        return self._get("/fapi/v1/allOrders", p)
    def position_risk(self, symbol=None):
        return self._get("/fapi/v2/positionRisk", {"symbol": symbol} if symbol else {})
import shutil
out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "kripto" / "islem-kayitlari-okuma"
out.mkdir(parents=True, exist_ok=True)
src = Path.home() / "kripto" / "islem-kayitlari" / "binance-kayitlari.json"
if not (out / "binance-kayitlari.json").exists() and src.exists(): shutil.copy(src, out)
ledger.refresh(ReadOnly(), out)
print("ok")
