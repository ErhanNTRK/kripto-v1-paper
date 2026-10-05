"""Would the live 2H trend system do better on BTC and ETH only? (user, 5 Oct 2026; the rotation test showed BTC held
beat the whole top 100 held by far). Same engine and rules as stop_placement.py with the live 5-ATR stop (8-ATR trail,
1% risk with quality sizing, pump filter, break-even at +2%, 3% minimum stop, daily -10% halt), 12-month windows
starting every month, per era (PIT_DIR). BTC is made tradable here (the engine normally reads it only as the market
filter). Universes: the point-in-time top 50 (live), its top 10 + BTC, BTC + ETH only.
Binance's minimum order (BTC 100 USDT, ETH 20 USDT notional) is applied as live; with ~200 USDT a 5-ATR BTC stop
often needs more risk than 1% allows, so a second run lifts the minimums (= a bigger wallet)."""
import json, sys, time
from collections import defaultdict, deque
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import rule_fix_test as R
import stop_placement as SP
import crypto_v1.research_v5 as rv5
from crypto_v1.research_v5 import ShortWindowLongModel as M
from crypto_v1.github_worker import load_2h_strategy


def prepare_with_btc(cfg, stop_mult=5.0):
    """rule_fix_test.prepare for 2h, plus BTCUSDT's own entries and exits."""
    D = R.prepare("2h", cfg, stop_mult)
    raw = json.loads((R.PIT / "data_2h.json").read_text(encoding="utf-8"))
    rows = rv5.symmetric_features(raw["BTCUSDT"], cfg)
    lows, sl = deque(maxlen=12), set()
    for f in rows:
        t = f["t"]; lows.append(f["l"])
        if t < R.START - 30 * R.DAY: continue
        if M.sell(f, f, cfg): sl.add(t)
        if M.buy(f, f, cfg):
            vr = f["v"] / f["volume_avg"] if f.get("volume_avg") else 0
            pump = f["c"] / min(lows) - 1 if min(lows) > 0 else 0
            D["ent"][t].append(("BTCUSDT", f["c"] - stop_mult * f["atr"], f.get("signal_score", vr), f.get("breaks_up", 0), pump))
    D["sell"]["BTCUSDT"] = sl
    return D


def main():
    cfg = dict(load_2h_strategy(relax=False), entry_mode="breakout")
    D = prepare_with_btc(cfg)
    live_daily = SP.DAILY
    universes = {
        "ilk 50 (canli)": live_daily,
        "ilk 10 + BTC": {d: set(R.ORDERED[d][:10]) | {"BTCUSDT"} for d in R.ORDERED},
        "sadece BTC + ETH": {d: {"BTCUSDT", "ETHUSDT"} for d in R.ORDERED},
    }
    for label, minimums in (("Binance minimum emir tutarlari ile (bugunku ~200 USDT cuzdan)", dict(R.MIN_NOTIONAL)),
                            ("minimumlar kaldirilmis (daha buyuk cuzdan gibi)", {})):
        print(f"\n--- {R.TAG}: {label} ---", flush=True)
        SP.MIN_NOTIONAL.clear(); SP.MIN_NOTIONAL.update(minimums)
        for name, daily in universes.items():
            SP.DAILY = daily
            rets, st, stops, again = SP.windows(D, mode="atr", mult=5.0)
            print(R.line(name, rets, st) + f" | yilda {stops:.0f} zararli stop", flush=True)
    SP.DAILY = live_daily


if __name__ == "__main__":
    main()
