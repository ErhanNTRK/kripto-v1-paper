"""Cost-aware six-hour trend candidate for BTC, ETH and SOL; research only."""
import json
from pathlib import Path

from .backtest import metrics, run
from .indicators import features
from .strategy import initial_stop

SIX_HOURS = 21_600_000
PAIR_ALLOWLIST = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


def six_hour(rows):
    groups = {}
    for row in rows:
        groups.setdefault(row["t"] // SIX_HOURS * SIX_HOURS, []).append(row)
    result = []
    for t in sorted(groups):
        bars = groups[t]
        if len(bars) != 24 or any(bars[i]["t"] + 900_000 != bars[i + 1]["t"] for i in range(23)):
            continue
        result.append({"t": t, "o": bars[0]["o"], "h": max(x["h"] for x in bars),
                       "l": min(x["l"] for x in bars), "c": bars[-1]["c"],
                       "v": sum(x["v"] for x in bars)})
    return result


class CostAwareTrend:
    @staticmethod
    def features(rows, config):
        output = features(rows, config)
        for i, row in enumerate(output):
            row["break20"] = i >= 20 and row["c"] > max(x["h"] for x in rows[i-20:i])
            row["exit20"] = min(x["l"] for x in rows[i-20:i]) if i >= 20 else None
        return output

    @staticmethod
    def buy(row, btc, config):
        if not row.get("ema200") or not row.get("atr") or not row.get("break20"):
            return False
        btc_up = btc and btc.get("ema200") and btc["c"] > btc["ema50"] > btc["ema200"]
        trend = row["c"] > row["ema20"] > row["ema50"] > row["ema200"]
        stop = initial_stop(row, config)
        # Estimated 2R gross move must clear a conservative 1% round-trip hurdle.
        return bool(btc_up and trend and stop > 0 and 2 * (row["c"] - stop) / row["c"] >= 0.01)

    @staticmethod
    def sell(row, btc, config=None):
        return (row.get("exit20") is None or row["c"] < row["ema20"]
                or row["c"] < row["exit20"])

    stop = staticmethod(initial_stop)


def evaluate_v3(data, config, output):
    aggregated = {symbol: six_hour(data[symbol]) for symbol in PAIR_ALLOWLIST if symbol in data}
    if set(aggregated) != set(PAIR_ALLOWLIST):
        raise ValueError("BTC, ETH and SOL data are required")
    times = [row["t"] for row in aggregated["BTCUSDT"]]
    if len(times) < 600:
        raise ValueError("At least 600 complete six-hour bars are required")
    start_index = 200
    split = times[start_index + int((len(times) - start_index) * 0.7)]
    segments = {"full": (times[start_index], times[-1] + SIX_HOURS),
                "development": (times[start_index], split),
                "holdout": (split, times[-1] + SIX_HOURS)}
    result = {"model": "six_hour_cost_aware_trend_btc_eth_sol", "segments": {}}
    for name, bounds in segments.items():
        result["segments"][name] = metrics(run(aggregated, list(PAIR_ALLOWLIST), config,
                                                       *bounds, CostAwareTrend, SIX_HOURS), config)
    stressed = dict(config, fee=config["fee"] * 2, slippage=config["slippage"] * 2)
    result["segments"]["holdout_double_cost"] = metrics(
        run(aggregated, list(PAIR_ALLOWLIST), stressed, *segments["holdout"], CostAwareTrend, SIX_HOURS), stressed)
    path = Path(output); path.mkdir(parents=True, exist_ok=True)
    (path / "report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
