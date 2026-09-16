import unittest
from unittest.mock import Mock
from crypto_v1.binance_trade import OrderRejected, OrderStateUnknown
from crypto_v1.live_monitor import exit_decision, execute_exit

P={"symbol":"SOLUSDT","entry":100,"stop_price":95,"quantity":"0.1","stop_client_id":"kv1s7"}
F={"c":111,"atr":2,"ema20":105,"rsi":60}
B={"c":100,"ema20":90,"ema50":80,"ema200":70}
C={"minimum_reward_risk":2,"trailing_atr":2}

class LiveMonitorTests(unittest.TestCase):
    def test_target_and_trailing_and_trend(self):
        self.assertEqual(exit_decision(P,F,B,111,C),"take_profit")
        self.assertEqual(exit_decision(P,dict(F,c=104,ema20=100),B,110,C),"trailing_profit")
        self.assertEqual(exit_decision(P,dict(F,c=103,ema20=104),B,104,C),"profit_signal")
        self.assertIsNone(exit_decision(P,dict(F,c=103,ema20=100),B,103,C))
    def test_sell_fn_override_replaces_default_ema_exit(self):
        # Price/EMA say hold (default sell_signal would return False here), but a
        # custom sell_fn (e.g. a Donchian model's) can still force the trend exit.
        held = dict(F, c=103, ema20=100)
        self.assertIsNone(exit_decision(P, held, B, 103, C))
        self.assertEqual(exit_decision(P, held, B, 103, C, sell_fn=lambda f, b: True), "profit_signal")

    def test_cancel_stop_then_sell_once(self):
        e=Mock(); e.cancel.return_value={"status":"CANCELED"}
        e.query.side_effect=OrderRejected(-2013); e.market_sell.return_value={"status":"FILLED"}
        r=execute_exit(P,"take_profit",e)
        self.assertEqual(r["status"],"sold"); e.market_sell.assert_called_once()
    def test_ambiguous_cancel_is_queried(self):
        e=Mock(); e.cancel.side_effect=OrderStateUnknown("unknown")
        e.query.return_value={"status":"FILLED"}
        self.assertEqual(execute_exit(P,"emergency_risk",e)["status"],"already_stopped")
        e.market_sell.assert_not_called()
