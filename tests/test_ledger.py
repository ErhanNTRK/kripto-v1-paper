import tempfile
import unittest
from pathlib import Path

from crypto_v1.ledger import round_trips, write_reports


def fill(fid, symbol, side, qty, price, t, order, pnl="0", fee="0.01"):
    return {"id": fid, "symbol": symbol, "side": side, "qty": str(qty), "price": str(price), "time": t,
            "orderId": order, "realizedPnl": pnl, "commission": fee}


class LedgerTests(unittest.TestCase):
    """24 Sep 2026: every trade on record with its USDT result, grouped from
    Binance's own fills into round trips."""

    STORE = {
        "fills": {
            "1": fill(1, "ZROUSDT", "BUY", 3.4, 1.5164, 1000, 11),
            "2": fill(2, "ZROUSDT", "SELL", 3.4, 1.60, 5000, 12, pnl="0.284"),
            "3": fill(3, "NILUSDT", "BUY", 108, 0.1079, 2000, 21),
            "4": fill(4, "NILUSDT", "SELL", 108, 0.0944, 3000, 22, pnl="-1.458"),
            "5": fill(5, "ARBUSDT", "BUY", 22.4, 0.2207, 4000, 31),
        },
        "clients": {"11": "kv1fl201821790186400000", "12": "kv1fy201821790186400000",
                    "21": "kv1fl297791790150400000", "22": "web_manual_close", "31": "kv1fl4abc"},
        "income": {"f1": {"incomeType": "FUNDING_FEE", "symbol": "ZROUSDT", "income": "-0.002", "time": 3000}},
    }

    def test_fills_become_round_trips_with_net_usdt(self):
        closed, still_open = round_trips(self.STORE)
        self.assertEqual([t["symbol"] for t in closed], ["NILUSDT", "ZROUSDT"])
        zro = closed[1]
        self.assertAlmostEqual(zro["realized"] - zro["commission"] + zro["funding"], 0.284 - 0.02 - 0.002)
        self.assertEqual([t["symbol"] for t in still_open], ["ARBUSDT"])

    def test_report_names_the_system_and_how_it_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows, _ = write_reports(self.STORE, Path(tmp))
            summary = (Path(tmp) / "ozet.txt").read_text(encoding="utf-8")
            self.assertTrue((Path(tmp) / "islemler.csv").exists())
        by_coin = {r["coin"]: r for r in rows}
        self.assertEqual(by_coin["ZROUSDT"]["sistem"], "2H")
        self.assertEqual(by_coin["ZROUSDT"]["kapanis_turu"], "cikis kurali")
        self.assertEqual(by_coin["NILUSDT"]["kapanis_turu"], "elle")
        self.assertIn("Kapanmis islem: 2", summary)

    def test_a_partial_exit_keeps_the_trip_open(self):
        store = {"fills": {"1": fill(1, "X", "BUY", 10, 1, 1, 1), "2": fill(2, "X", "SELL", 4, 1.1, 2, 2)},
                 "clients": {}, "income": {}}
        closed, still_open = round_trips(store)
        self.assertEqual(closed, [])
        self.assertEqual(len(still_open), 1)


if __name__ == '__main__':
    unittest.main()
