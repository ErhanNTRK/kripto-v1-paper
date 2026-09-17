"""User-requested exploratory variant (17 Sep 2026). NOT pre-registered like
V1/V2/V3 -- built specifically to test, with real numbers instead of
argument, three changes the user asked for:

1. No breakout confirmation: buys when price reclaims its own recent
   AVERAGE (ema20) instead of waiting for a Donchian breakout above the
   recent HIGH -- a materially earlier ('don't wait for the old high')
   entry than the tested V2 model.
2. No market-wide uptrend requirement: unlike btc_ok() (BTC's EMAs must be
   stacked upward), this only requires BTC not be in a meaningful decline
   of its own (more than 3% below its own 20-bar average).
3. Leverage is deliberately NOT included here -- signal quality is tested
   unleveraged first, per the agreed plan; leverage changes financial risk
   independent of signal quality and needs its own separate infrastructure
   and validation.

Working hypothesis going in: earlier V1 'gevsek' (loosened) variants did
not outperform the stricter ones, so this is expected to score similarly
or worse, not better -- tested to find out, not to confirm that.
"""
from .indicators import features
from .strategy import initial_stop
from .research_v2 import aggregate, FOUR_HOUR
from . import walkforward as wf

BTC_DECLINE_FLOOR = 0.97  # BTC more than 3% below its own 20-bar average counts as a decline


class EarlyReversalModel:
    features = staticmethod(features)

    @staticmethod
    def buy(row, btc, config):
        if not row.get("atr") or not row.get("ema20") or row.get("rsi") is None:
            return False
        if not (row["c"] > row["ema20"] and 40 <= row["rsi"] <= 70):
            return False
        if btc and btc.get("ema20") and btc["c"] < btc["ema20"] * BTC_DECLINE_FLOOR:
            return False
        stop = initial_stop(row, config)
        return stop > 0 and 2 * (row["c"] - stop) / row["c"] >= 0.006

    @staticmethod
    def sell(row, btc):
        return row.get("ema20") is not None and row["c"] < row["ema20"]

    stop = staticmethod(initial_stop)


def evaluate_walkforward(data, symbols, config, manifest, count=5, min_trades=8,
                          interval=FOUR_HOUR, warm=100):
    agg_data = {symbol: aggregate(rows, interval) for symbol, rows in data.items()}
    symbols = [s for s in symbols if len(agg_data.get(s, [])) >= 200]
    min_bars = warm + count * min_trades * 4
    return wf.evaluate(agg_data, symbols, config, manifest, count, model=EarlyReversalModel,
                        interval=interval, warm=warm, min_trades=min_trades, min_bars=min_bars)
