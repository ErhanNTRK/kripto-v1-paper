"""Shadow trades (user's request, 25 Sep 2026): signals the bot could not
take for lack of capital -- the size fell under Binance's minimum, or the
margin ran short. Each is recorded when it is skipped and later played out
with the bot's own exit rules, so the user can see what the money would have
done once there is enough of it to take them.

The replay mirrors the live long exit: the protective stop at the signal's
2 ATR level (hit intrabar), the resting stop ratcheted to best price minus
trailing_atr x resting_stop_trail_multiple ATR once the trade is 1R ahead
(hit intrabar), and exit_decision's close-based trend, trailing and BTC
exits at the bar close. Entry is the signal close; the live entry could have
been up to 0.5% away from it."""
import json
from decimal import Decimal
from pathlib import Path

from .live_monitor import exit_decision
from .research_v5 import ShortWindowLongModel

# Rejections that mean "not enough capital", not "no trade": the only ones a
# larger account would have taken.
CAPITAL_REASONS = {
    "risk target below Binance minimum": "tutar Binance minimumunun altinda",
    "order is below Binance minimums": "teminat yetersiz",
    "plan exceeds available margin": "teminat yetersiz",
}
# How far back a replay reads before the signal, for the indicators.
WARMUP_BARS = 260


def _load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save(path, rows):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(rows), encoding="utf-8")
    tmp.replace(path)


def record(path, tag, candidate, side, reason, now_ms):
    """Keep one skipped long for replay. False when it is not a capital skip,
    not a long, or already kept."""
    if reason not in CAPITAL_REASONS or side != "long" or path is None:
        return False
    if candidate.get("close") is None or candidate.get("stop") is None:
        return False
    rows = _load(path)
    key = (str(tag), candidate["symbol"], int(candidate["created_at"]))
    if any((r["tag"], r["symbol"], r["signal_time"]) == key for r in rows):
        return False
    rows.append({"tag": str(tag), "symbol": candidate["symbol"], "signal_time": int(candidate["created_at"]),
                 "entry": float(candidate["close"]), "stop": float(candidate["stop"]),
                 "breaks_up": candidate.get("breaks_up"), "reason": reason, "recorded_at": int(now_ms)})
    _save(path, rows)
    return True


def replay(trade, rows, btc_rows, strategy_config, resting_multiple, interval):
    """Play one shadow trade forward on closed bars. Returns
    {"status": "closed"|"open", "exit", "exit_time", "reason", "r"}; r is the
    result in units of the initial risk (entry - stop), before fees."""
    entry, stop = Decimal(str(trade["entry"])), Decimal(str(trade["stop"]))
    risk = entry - stop
    if risk <= 0:
        return {"status": "invalid"}
    config = dict(strategy_config, cap_at_target=False)
    coin = {f["t"]: f for f in ShortWindowLongModel.features(rows, strategy_config)}
    btc = {f["t"]: f for f in ShortWindowLongModel.features(btc_rows, strategy_config)}
    trail = Decimal(str(strategy_config["trailing_atr"]))
    resting, best = stop, entry
    last_close = entry
    for t in sorted(coin):
        if t < trade["signal_time"] or t not in btc:
            continue
        f = coin[t]
        low, high, close = (Decimal(str(f[k])) for k in ("l", "h", "c"))
        if low <= resting:
            price = min(Decimal(str(f["o"])), resting)
            return _closed(price, t + interval, "stop", entry, risk)
        best = max(best, high)
        atr = Decimal(str(f["atr"] or 0))
        if best >= entry + risk and atr > 0:
            resting = max(resting, best - trail * Decimal(str(resting_multiple)) * atr)
        judged = resting if resting < entry else entry - entry * Decimal("1e-9")
        reason = exit_decision({"entry": entry, "stop_price": judged}, f, btc[t], best, config,
                               sell_fn=lambda row, b: ShortWindowLongModel.sell(row, b, strategy_config))
        last_close = close
        if reason:
            return _closed(close, t + interval, reason, entry, risk)
    return {"status": "open", "exit": float(last_close), "exit_time": None, "reason": None,
            "r": float((last_close - entry) / risk)}


def _closed(price, when, reason, entry, risk):
    return {"status": "closed", "exit": float(price), "exit_time": int(when), "reason": reason,
            "r": float((price - entry) / risk)}


# Binance's smallest order per contract, where it is not the usual 5 USDT.
MIN_NOTIONAL = {"ETHUSDT": 20.0, "BCHUSDT": 20.0, "LTCUSDT": 20.0, "BTCUSDT": 100.0}


def usdt_at_minimum(trade, result):
    """What the trade would have made at the smallest order Binance takes --
    the size the bot will first be able to afford."""
    quantity = MIN_NOTIONAL.get(trade["symbol"], 5.0) * 1.03 / trade["entry"]
    return quantity * (result["exit"] - trade["entry"]) - quantity * trade["entry"] * 0.002  # ~fees both ways


DAY_MS = 86_400_000


def evaluate(path, fetch, systems, now_ms, days=30):
    """Replay every shadow trade of the last `days`. systems maps a tag to
    (strategy_config, resting_multiple, interval); fetch(symbol, start, end,
    interval) returns closed candles."""
    trades = [t for t in _load(path) if t["signal_time"] >= now_ms - days * DAY_MS and t["tag"] in systems]
    btc_cache, results = {}, []
    for trade in trades:
        config, multiple, interval = systems[trade["tag"]]
        start = trade["signal_time"] - WARMUP_BARS * interval
        end = now_ms // interval * interval
        if trade["tag"] not in btc_cache:
            earliest = min(t["signal_time"] for t in trades if t["tag"] == trade["tag"]) - WARMUP_BARS * interval
            btc_cache[trade["tag"]] = fetch("BTCUSDT", earliest, end, interval)
        rows = fetch(trade["symbol"], start, end, interval)
        results.append((trade, replay(trade, rows, btc_cache[trade["tag"]], config, multiple, interval)))
    return results


def summary_line(results):
    """One Telegram line for the morning summary, or None."""
    valid = [(t, r) for t, r in results if r.get("status") in ("closed", "open")]
    if not valid:
        return None
    closed = sum(1 for _, r in valid if r["status"] == "closed")
    total_r = sum(r["r"] for _, r in valid)
    usdt = sum(usdt_at_minimum(t, r) for t, r in valid)
    return (f"Sermaye yetmedigi icin atlanan {len(valid)} islem (son 30 gun): alsaydik toplam {total_r:+.1f}R, "
            f"en kucuk tutarla ~{usdt:+.2f} USDT ({closed} kapandi, {len(valid) - closed} suruyor)")


def main():
    """Table of every shadow trade, for reviewing by hand."""
    import time
    from .data import candles
    from .research_v2 import FOUR_HOUR, TWO_HOUR
    runtime = Path("runtime")
    four = json.loads(Path("config_v5_long.json").read_text(encoding="utf-8"))
    two = json.loads(Path("config_v5_long_2h.json").read_text(encoding="utf-8"))
    multiple = json.loads(Path("short_live_config.json").read_text(encoding="utf-8")).get(
        "resting_stop_trail_multiple", 1)
    systems = {"4": (four, multiple, FOUR_HOUR), "2": (two, multiple, TWO_HOUR)}
    results = evaluate(runtime / "shadow_trades.json", candles, systems, int(time.time() * 1000), days=3650)
    for trade, result in results:
        print(f"{trade['tag']}H {trade['symbol']:12} giris {trade['entry']:<12g} stop {trade['stop']:<12.6g} "
              f"{result.get('status'):7} cikis {result.get('exit', 0):<12.6g} {result.get('r', 0):+6.2f}R "
              f"{usdt_at_minimum(trade, result) if 'exit' in result else 0:+7.3f} USDT  {result.get('reason') or ''}")
    print(summary_line(results) or "Kayitli atlanan islem yok.")


if __name__ == "__main__":
    main()
