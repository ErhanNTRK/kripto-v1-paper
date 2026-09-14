"""Pre-registered hourly Donchian ensemble candidate; research/paper only."""
import json
from pathlib import Path

from .backtest import metrics, run
from .indicators import features
from .strategy import btc_ok, initial_stop

HOUR = 3_600_000


def hourly(rows):
    groups = {}
    for row in rows:
        groups.setdefault(row["t"] // HOUR * HOUR, []).append(row)
    output = []
    for t in sorted(groups):
        bars = groups[t]
        if len(bars) != 4 or any(bars[i]["t"] + 900_000 != bars[i + 1]["t"] for i in range(3)):
            continue
        output.append({"t": t, "o": bars[0]["o"], "h": max(x["h"] for x in bars),
                       "l": min(x["l"] for x in bars), "c": bars[-1]["c"],
                       "v": sum(x["v"] for x in bars)})
    return output


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
    def sell(row, btc):
        return (not btc_ok(btc) or row.get("donchian_exit") is None
                or row["c"] < row["donchian_exit"])

    stop = staticmethod(initial_stop)


def evaluate(data, symbols, config, output):
    hourly_data = {symbol: hourly(rows) for symbol, rows in data.items()}
    symbols = [s for s in symbols[:20] if len(hourly_data.get(s, [])) >= 400]
    times = [r["t"] for r in hourly_data["BTCUSDT"]]
    warm = max(200, len(times) // 5)
    split = times[warm + int((len(times) - warm) * 0.7)]
    segments = {"full": (times[warm], times[-1] + HOUR),
                "development": (times[warm], split),
                "holdout": (split, times[-1] + HOUR)}
    result = {"model": "hourly_donchian_20_40_80_top20", "symbols": symbols, "segments": {}}
    for name, (start, end) in segments.items():
        state = run(hourly_data, symbols, config, start, end, DonchianModel, HOUR)
        result["segments"][name] = metrics(state, config)
    stressed = dict(config, fee=config["fee"] * 2, slippage=config["slippage"] * 2)
    state = run(hourly_data, symbols, stressed, *segments["holdout"], DonchianModel, HOUR)
    result["segments"]["holdout_double_cost"] = metrics(state, stressed)
    path = Path(output); path.mkdir(parents=True, exist_ok=True)
    (path / "report.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result
