"""Should the 4H system wait for the recent high? (user, 5 Oct 2026, on AAVE: it broke only its 10-bar high 183.09,
the 20/40/200-bar high 187.40 still above; "it has to clear the highest level first"). The live 4H rules buy on
breaks_up >= 1 (any of the 10/20/40-bar Donchian highs) and SKIP a close above the 200-bar high (skip_first_time_high,
added 23 Sep from NIL/SAGA on one 400-day walk-forward) -- so live it buys under the resistance and refuses the break.

Same engine as rule_fix_test.py with the live 4H settings (2 ATR stop, 6 ATR trail, 6 positions, 1% risk with quality
sizing, ETH/BTC gate, 3% minimum stop, 24h pump filter 20%, stop to entry at +2%), 12-month windows every month,
per era (PIT_DIR). Entries are found once with the first-time-high filter OFF and tagged; the variants filter them:
  now            breaks >= 1, skip first-time highs                 (live)
  20-bar         breaks >= 2 (the 20-bar high cleared), skip first-time highs
  allow-high     breaks >= 1, first-time highs allowed
  20-bar+high    breaks >= 2, first-time highs allowed               (the user's rule: clear the recent high, then buy)
  all three      breaks == 3, first-time highs allowed"""
import json, sys, time
from collections import defaultdict, deque
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import rule_fix_test as R
import crypto_v1.research_v5 as rv5
from crypto_v1.research_v5 import ShortWindowLongModel as M
from crypto_v1.risk import validate_config


def prepare_tagged(cfg, stop_mult=2.0):
    t0 = time.time()
    feat_cfg = dict(cfg, skip_first_time_high=True)       # computes range_high on every row
    buy_cfg = dict(cfg, skip_first_time_high=False)       # ...but lets the entry through
    raw = json.loads((R.PIT / "data_4h.json").read_text(encoding="utf-8"))
    btc_rows = rv5.symmetric_features(raw.pop("BTCUSDT"), feat_cfg)
    btc = {r["t"]: r for r in btc_rows}
    bars = {"BTCUSDT": {r["t"]: (r["o"], r["h"], r["l"], r["c"], r.get("atr")) for r in btc_rows if r["t"] >= R.START - 30 * R.DAY}}
    ent, sell = defaultdict(list), {}
    for s in list(raw):
        rows = R.cut(raw.pop(s))
        if len(rows) < 250 or (s not in R.EVER and s != "ETHUSDT"):
            continue
        b, sl, lows = {}, set(), deque(maxlen=6)
        for f in rv5.symmetric_features(rows, feat_cfg):
            t = f["t"]; lows.append(f["l"])
            if t < R.START - 30 * R.DAY:
                continue
            b[t] = (f["o"], f["h"], f["l"], f["c"], f.get("atr"))
            bt = btc.get(t)
            if bt is None or s not in R.EVER:
                continue
            if M.sell(f, bt, buy_cfg):
                sl.add(t)
            if M.buy(f, bt, buy_cfg):
                vr = f["v"] / f["volume_avg"] if f.get("volume_avg") else 0
                pump = f["c"] / min(lows) - 1 if min(lows) > 0 else 0
                first = f.get("range_high") is not None and f["c"] > f["range_high"]
                ent[t].append((s, f["c"] - stop_mult * f["atr"], f.get("signal_score", vr), f.get("breaks_up", 0), pump, first))
        bars[s], sell[s] = b, sl
    iv = 4 * 3_600_000
    def dcl(sym):
        return {(t + iv) // R.DAY * R.DAY: v[3] for t, v in bars[sym].items() if (t + iv) % R.DAY == 0}
    e, bb = dcl("ETHUSDT"), dcl("BTCUSDT")
    gate, m = {}, None
    for d in sorted(set(e) & set(bb)):
        r = e[d] / bb[d]; m = r if m is None else m + (r - m) * 2 / 51; gate[d] = r > m
    times = sorted(t for t in bars["BTCUSDT"] if R.START <= t < R.END)
    print(f"{R.TAG} 4h: hazir {round(time.time() - t0)} sn", flush=True)
    return dict(bars=bars, ent=ent, sell=sell, gate=gate, times=times, iv=iv)


VARIANTS = (("simdiki (1/3 kirilim, ilk-zirve alinmaz)", lambda b, ft: b >= 1 and not ft),
            ("20 mum zirvesi kirilmali, ilk-zirve alinmaz", lambda b, ft: b >= 2 and not ft),
            ("1/3 kirilim, ilk-zirve de alinir", lambda b, ft: b >= 1),
            ("KULLANICI: 20 mum zirvesi kirilmali, ilk-zirve alinir", lambda b, ft: b >= 2),
            ("3/3 kirilim, ilk-zirve alinir", lambda b, ft: b >= 3))


def main():
    C4 = validate_config(json.loads(Path("config_v5_long.json").read_text(encoding="utf-8")))
    D = prepare_tagged(dict(C4, entry_mode="breakout"))
    all_ent = D["ent"]
    n_first = sum(x[5] for v in all_ent.values() for x in v); n_all = sum(len(v) for v in all_ent.values())
    print(f"{R.TAG}: {n_all} sinyal, {n_first} tanesi ilk-zirve kirilimi\n", flush=True)
    for name, keep in VARIANTS:
        D["ent"] = {t: [x[:5] for x in v if keep(x[3], x[5])] for t, v in all_ent.items()}
        rets, st = R.windows(D, trail=6.0, maxpos=6, gate=True, min_stop=0.03, pump=0.20, be=0.02)
        print(R.line(f"4H {name}", rets, st), flush=True)


if __name__ == "__main__":
    main()
