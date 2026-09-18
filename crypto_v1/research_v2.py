"""Pre-registered hourly Donchian ensemble candidate; research/paper only."""
import json
from pathlib import Path

from .backtest import metrics, run
from .indicators import features
from .strategy import btc_ok, initial_stop
from . import walkforward as wf

HOUR = 3_600_000
FOUR_HOUR = 14_400_000
DAY = 86_400_000


def aggregate(rows, interval):
    """Group 15m rows into `interval`-sized bars, keeping only fully-complete,
    contiguous groups (no partial trailing bar, no gaps)."""
    n = interval // 900_000
    groups = {}
    for row in rows:
        groups.setdefault(row["t"] // interval * interval, []).append(row)
    output = []
    for t in sorted(groups):
        bars = groups[t]
        if len(bars) != n or any(bars[i]["t"] + 900_000 != bars[i + 1]["t"] for i in range(n - 1)):
            continue
        output.append({"t": t, "o": bars[0]["o"], "h": max(x["h"] for x in bars),
                       "l": min(x["l"] for x in bars), "c": bars[-1]["c"],
                       "v": sum(x["v"] for x in bars)})
    return output


def hourly(rows):
    return aggregate(rows, HOUR)


class DonchianModel:
    periods = (20, 40, 80)

    @staticmethod
    def features(rows, config):
        output = features(rows, config)
        for i, row in enumerate(output):
            breaks = []
            lows = []
            for period in DonchianModel.periods:
                breaks.append(i >= period and row["c"] > max(x["h"] for x in rows[i-period:i]))
                lows.append(min(x["l"] for x in rows[i-period:i]) if i >= period else None)
            row["donchian_breaks"] = sum(breaks)
            row["donchian_exit"] = lows[1]
            row["signal_score"] = row["donchian_breaks"] + (row["v"] / row["volume_avg"] if row.get("volume_avg") else 0)
        return output

    @staticmethod
    def buy(row, btc, config):
        if not btc_ok(btc) or not row.get("atr") or row.get("donchian_breaks", 0) < 2:
            return False
        # Require expected 2R move to exceed a pre-registered 0.60% round-trip cost hurdle.
        stop = initial_stop(row, config)
        return stop > 0 and 2 * (row["c"] - stop) / row["c"] >= 0.006

    @staticmethod
    def sell(row, btc, config=None):
        return (not btc_ok(btc) or row.get("donchian_exit") is None
                or row["c"] < row["donchian_exit"])

    stop = staticmethod(initial_stop)


def evaluate(data, symbols, config, output):
    hourly_data = {symbol: hourly(rows) for symbol, rows in data.items()}
    # symbols already reflects config['top_n'] from the fetch-time universe
    # selection (crypto_v1.data.universe) -- do not re-cap it here.
    symbols = [s for s in symbols if len(hourly_data.get(s, [])) >= 400]
    times = [r["t"] for r in hourly_data["BTCUSDT"]]
    warm = max(200, len(times) // 5)
    split = times[warm + int((len(times) - warm) * 0.7)]
    segments = {"full": (times[warm], times[-1] + HOUR),
                "development": (times[warm], split),
                "holdout": (split, times[-1] + HOUR)}
    result = {"model": "hourly_donchian_20_40_80", "symbols": symbols, "segments": {}}
    for name, (start, end) in segments.items():
        state = run(hourly_data, symbols, config, start, end, DonchianModel, HOUR)
        result["segments"][name] = metrics(state, config)
    stressed = dict(config, fee=config["fee"] * 2, slippage=config["slippage"] * 2)
    state = run(hourly_data, symbols, stressed, *segments["holdout"], DonchianModel, HOUR)
    result["segments"]["holdout_double_cost"] = metrics(state, stressed)
    path = Path(output); path.mkdir(parents=True, exist_ok=True)
    (path / "report.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


def evaluate_walkforward(data, symbols, config, manifest, count=5, min_trades=15):
    """Same pre-registered Donchian model, but scored with independent multi-window
    walk-forward (see crypto_v1.walkforward) instead of a single development/holdout split."""
    hourly_data = {symbol: hourly(rows) for symbol, rows in data.items()}
    symbols = [s for s in symbols if len(hourly_data.get(s, [])) >= 400]
    result = wf.evaluate(hourly_data, symbols, config, manifest, count,
                          model=DonchianModel, interval=HOUR, warm=200, min_trades=min_trades)
    return result


def evaluate_timeframe_walkforward(data, symbols, config, manifest, interval, count=5,
                                    min_trades=8, warm=100):
    """Same Donchian rules, same model, run on a coarser timeframe than the
    pre-registered hourly version -- to check whether lower trade frequency
    (and therefore a smaller total cost drag) is enough to clear the same
    multi-window + 2x-cost-stress bar."""
    agg_data = {symbol: aggregate(rows, interval) for symbol, rows in data.items()}
    symbols = [s for s in symbols if len(agg_data.get(s, [])) >= 200]
    min_bars = warm + count * min_trades * 4
    return wf.evaluate(agg_data, symbols, config, manifest, count, model=DonchianModel,
                        interval=interval, warm=warm, min_trades=min_trades, min_bars=min_bars)
