import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from crypto_v1.binance_trade import OrderRejected
from crypto_v1.spike_rule import TWO_HOUR_MS, LiveSpikeRule, spike_candidates

T = 1_790_640_000_000


def feats(n=120, big=None, red=None, ichimoku=True, t=T):
    rows = [{"t": t - (n - 1 - i) * TWO_HOUR_MS, "o": 10.0, "h": 10.5, "l": 9.5, "c": 10.0, "atr": 0.5,
             "ema50": 9.0, "ichimoku_ok": True} for i in range(n)]
    rows[-2].update(big or {"o": 10.0, "c": 12.0, "h": 12.2})          # body 2.0 = 4 ATR, new high
    rows[-1].update(red or {"o": 11.9, "c": 11.5, "h": 12.0}, ichimoku_ok=ichimoku)
    return rows


class SpikeCandidateTests(unittest.TestCase):
    def _run(self, coin, btc_close=10.0):
        btc = feats()
        btc[-1].update(c=btc_close, ema50=9.0)
        data = {"BTCUSDT": [0] * 120, "SOLUSDT": [0] * 120}
        with patch("crypto_v1.research_v5.symmetric_features", side_effect=lambda rows, cfg: btc if rows is data["BTCUSDT"] else coin):
            return spike_candidates(data, ["SOLUSDT"], {})

    def test_big_green_new_high_then_red_is_a_candidate(self):
        found = self._run(feats())
        self.assertEqual([c["symbol"] for c in found], ["SOLUSDT"])
        self.assertEqual(found[0]["close"], 11.5)

    def test_no_candidate_when_the_body_is_small_the_high_is_not_new_the_bar_is_green_or_ichimoku_is_not_bullish(self):
        self.assertEqual(self._run(feats(big={"o": 10.0, "c": 11.0, "h": 12.2})), [])          # 2 ATR only
        high_before = feats(); high_before[-30]["h"] = 20.0
        self.assertEqual(self._run(high_before), [])
        self.assertEqual(self._run(feats(red={"o": 11.5, "c": 11.9, "h": 12.0})), [])
        self.assertEqual(self._run(feats(ichimoku=False)), [])

    def test_no_candidate_while_btc_is_under_its_ema50(self):
        self.assertEqual(self._run(feats(), btc_close=8.0), [])


class FakeExecutor:
    def __init__(self):
        self.amounts, self.open, self.calls, self.fail_stop = {}, {}, [], False

    def account(self):
        return {"positions": [{"symbol": s, "positionAmt": str(a), "entryPrice": "11.5"} for s, a in self.amounts.items()]}

    def open_orders(self):
        return [{"clientOrderId": cid, "symbol": s} for cid, s in self.open.items()]

    def set_isolated_margin(self, symbol):
        self.calls.append(("isolated", symbol))

    def set_leverage(self, symbol, lev):
        self.calls.append(("leverage", symbol, lev))

    def market_open_long(self, symbol, qty, cid):
        self.calls.append(("buy", symbol, qty, cid))
        self.amounts[symbol] = Decimal(qty)
        return {"executedQty": qty, "avgPrice": "11.5"}

    def protective_stop_for_long(self, symbol, qty, stop, cid):
        self.calls.append(("stop", symbol, qty, stop, cid))
        if self.fail_stop:
            raise OrderRejected(-2021)
        self.open[cid] = symbol

    def cancel(self, symbol, cid):
        self.calls.append(("cancel", symbol, cid))
        self.open.pop(cid, None)

    def market_close_long(self, symbol, qty, cid):
        self.calls.append(("close", symbol, qty, cid))
        self.amounts[symbol] = Decimal("0")
        return {"avgPrice": "12.0"}

    def query(self, symbol, cid):
        raise OrderRejected(-2013)


class FakeMarket:
    def rules(self, symbol):
        return {"tick_size": Decimal("0.001"), "step_size": Decimal("0.1"), "min_qty": Decimal("0.1"),
                "min_notional": Decimal("5")}

    def price(self, symbol):
        return 11.5


class LiveSpikeRuleTests(unittest.TestCase):
    CONFIG = {"mode": "live", "stop_atr": 2.0, "hold_hours": 24, "risk_fraction": 0.015, "max_age_minutes": 30}

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.ex, self.sent = FakeExecutor(), []
        self.now = [T + TWO_HOUR_MS + 120_000]           # 2 minutes after the red bar closed
        (self.dir / "signals.json").write_text(json.dumps(
            {"window": T, "candidates": [{"symbol": "SOLUSDT", "close": 11.5, "atr": 0.5, "time": T}]}))

    def _rule(self, config=None):
        return LiveSpikeRule(config or self.CONFIG, self.dir / "spike.json", self.dir / "signals.json", self.ex,
                             FakeMarket(), send=self.sent.append, clock=lambda: self.now[0] / 1000,
                             equity_fn=lambda: 150)

    def test_buys_sized_by_risk_with_a_two_atr_stop(self):
        state = self._rule().tick()
        buy = next(c for c in self.ex.calls if c[0] == "buy")
        # risk 1.5% of 150 = 2.25 USDT over (11.5 - 10.5) = 2.25 coins -> 2.2 at the 0.1 step
        self.assertEqual(buy[2], "2.2")
        self.assertTrue(buy[3].startswith("kv1fh"))
        stop = next(c for c in self.ex.calls if c[0] == "stop")
        self.assertEqual(Decimal(stop[3]), Decimal("10.5"))
        self.assertTrue(stop[4].startswith("kv1fj"))
        self.assertIn("SOLUSDT", state["positions"])
        self.assertTrue(any("ALDIM" in m for m in self.sent))

    def test_an_unfilled_looking_response_is_still_protected(self):
        buy = self.ex.market_open_long
        self.ex.market_open_long = lambda s, q, c: (buy(s, q, c), {"status": "NEW", "executedQty": "0"})[1]
        rule = self._rule()
        rule.settle_sleep = lambda s: None
        state = rule.tick()
        stop = next(c for c in self.ex.calls if c[0] == "stop")
        self.assertEqual((stop[2], Decimal(stop[3])), ("2.2", Decimal("10.5")))
        self.assertIn("SOLUSDT", state["positions"])

    def test_each_signal_is_taken_once(self):
        rule = self._rule()
        rule.tick(); rule.tick()
        self.assertEqual(len([c for c in self.ex.calls if c[0] == "buy"]), 1)

    def test_an_old_signal_or_a_busy_coin_is_skipped(self):
        self.now[0] = T + TWO_HOUR_MS + 45 * 60_000
        self._rule().tick()
        self.assertEqual([c for c in self.ex.calls if c[0] == "buy"], [])
        self.setUp()
        self.ex.open["kv1fq12341790640000000"] = "SOLUSDT"
        self._rule().tick()
        self.assertEqual([c for c in self.ex.calls if c[0] == "buy"], [])

    def test_sold_after_24_hours_and_stop_cancelled(self):
        rule = self._rule()
        rule.tick()
        self.now[0] = T + TWO_HOUR_MS + 24 * 3_600_000 + 1
        state = rule.tick()
        self.assertTrue(any(c[0] == "close" for c in self.ex.calls))
        self.assertTrue(any(c[0] == "cancel" and c[2].startswith("kv1fj") for c in self.ex.calls))
        self.assertEqual(state["closed"][0]["reason"], "sure doldu")

    def test_a_fired_stop_is_recorded(self):
        rule = self._rule()
        rule.tick()
        self.ex.amounts["SOLUSDT"] = Decimal("0")
        state = rule.tick()
        self.assertEqual(state["closed"][0]["reason"], "stop")

    def test_a_stop_that_cannot_be_placed_closes_at_market(self):
        self.ex.fail_stop = True
        state = self._rule().tick()
        self.assertTrue(any(c[0] == "close" for c in self.ex.calls))
        self.assertIn("acil kapatma", state["closed"][0]["reason"])

    def test_off_unless_live(self):
        self.assertIsNone(self._rule(dict(self.CONFIG, mode="off")).tick())
        self.assertEqual(self.ex.calls, [])


if __name__ == "__main__":
    unittest.main()
