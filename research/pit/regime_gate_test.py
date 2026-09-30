"""Automatic stop for new longs in a bad period (user, 30 Sep 2026: "when the bad period's data comes, automatic
longs should stop"). Every gate is computed from daily closes known at the start of the day (no look-ahead):
  ETH/BTC      ETH/BTC above its 50-day EMA (the 4H sleeve's live gate)
  breadth X%   at least X% of that day's top-50 close above their own 50-day average
  alts>BTC X%  at least X% of that day's top-50 beat BTC over the last 30 days
Both sleeves run the live rules (3% min stop, 24h pump filter, break-even at +2%). Reports the 12-month windows
of both eras, the most recent 12 and 6 months of data (Sep 2025 / Mar 2026 -> Aug 2026, the current period),
and the share of days each gate was closed."""
import json, statistics as S, sys
from pathlib import Path
from datetime import datetime, timezone
sys.path.insert(0, str(Path(__file__).parent))
import rule_fix_test as R
from rule_fix_test import DAY, DAILY, END, TAG, prepare, run, windows, validate_config, load_2h_strategy

LIVE = dict(min_stop=0.03, pump=0.20, be=0.02)


def daily_closes(D, sym):
    iv = D["iv"]
    return {(t + iv) // DAY * DAY: v[3] for t, v in D["bars"].get(sym, {}).items() if (t + iv) % DAY == 0}


def gates(D):
    closes = {s: daily_closes(D, s) for s in D["bars"]}
    btc = closes["BTCUSDT"]
    breadth, beat = {}, {}
    for d, syms in DAILY.items():
        above = n = win = m = 0
        b0, b1 = btc.get(d - 30 * DAY), btc.get(d)
        for s in syms:
            c = closes.get(s, {})
            hist = [c.get(d - k * DAY) for k in range(50)]
            if all(h is not None for h in hist):
                n += 1; above += hist[0] > sum(hist) / 50
            if b0 and b1 and c.get(d) and c.get(d - 30 * DAY):
                m += 1; win += c[d] / c[d - 30 * DAY] > b1 / b0
        breadth[d] = above / n if n >= 10 else None
        beat[d] = win / m if m >= 10 else None
    return breadth, beat


def gate_from(D, breadth, beat, eth=False, b_min=None, beat_min=None):
    g = {}
    for d in DAILY:
        ok = True
        if eth:
            ok &= D["gate"].get(d, False)
        if b_min is not None and breadth.get(d) is not None:
            ok &= breadth[d] >= b_min
        if beat_min is not None and beat.get(d) is not None:
            ok &= beat[d] >= beat_min
        g[d] = ok
    return g


ts = lambda y, m: int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp() * 1000)
GATES = (("kapi yok (2H canli)", {}),
         ("ETH/BTC", dict(eth=True)),
         ("genislik %50", dict(b_min=0.5)),
         ("genislik %60", dict(b_min=0.6)),
         ("altlar>BTC %40", dict(beat_min=0.4)),
         ("ETH/BTC + genislik %50", dict(eth=True, b_min=0.5)))


def recent(D, g, months_back, **kw):
    s0 = ts(2025, 9) if months_back == 12 else ts(2026, 3)
    c, cnt = run(dict(D, gate=g), gate=True, s0=s0, s1=END, **kw)
    w = [(t, e) for t, e in c if t >= s0]
    return 100 * w[-1][1] / w[0][1], cnt


if __name__ == "__main__":
    out = {}
    for fl, cfg, stop_mult, kw in (
            ("4h", dict(validate_config(json.loads(Path("config_v5_long.json").read_text(encoding="utf-8"))),
                        entry_mode="breakout"), 2.0, dict(trail=6.0, maxpos=6)),
            ("2h", dict(load_2h_strategy(relax=False), entry_mode="breakout"), 3.0, {})):
        D = prepare(fl, cfg, stop_mult)
        breadth, beat = gates(D)
        for name, gk in GATES:
            g = gate_from(D, breadth, beat, **gk)
            closed = 100 * sum(not v for d, v in g.items() if d >= R.START) / max(1, len(g))
            rets, st = windows(dict(D, gate=g), gate=True, **LIVE, **kw)
            key = f"{fl.upper()} {name}"
            out[key] = rets
            extra = ""
            if TAG == "2023-26":
                r12, _ = recent(D, g, 12, **LIVE, **kw); r6, _ = recent(D, g, 6, **LIVE, **kw)
                extra = f" | SON 12 AY {r12:4.0f}$  SON 6 AY {r6:4.0f}$"
                out[key + " son12"] = r12; out[key + " son6"] = r6
            print(R.line(key, rets, st) + f" | kapi kapali %{closed:.0f}" + extra, flush=True)
        del D
    print(f"\n=== {TAG}: TUM SISTEM 25/75 (4H ETH/BTC kapisi sabit, 2H kapisi degisiyor) ===")
    for name, _ in GATES:
        a, b = out["4H ETH/BTC"], out["2H " + name]
        rets = [0.25 * x + 0.75 * y for x, y in zip(a, b)]
        sr = sorted(rets)
        extra = ""
        if TAG == "2023-26":
            extra = (f" | SON 12 AY {0.25*out['4H ETH/BTC son12'] + 0.75*out['2H '+name+' son12']:4.0f}$"
                     f"  SON 6 AY {0.25*out['4H ETH/BTC son6'] + 0.75*out['2H '+name+' son6']:4.0f}$")
        print(f"2H {name:26} | 100$ 12 ay: tipik {100*(1+S.median(rets)):5.0f}$  en kotu {100*(1+sr[0]):4.0f}$  "
              f"en iyi {100*(1+sr[-1]):5.0f}$ | zararli yil %{100*sum(x<0 for x in rets)/len(rets):3.0f}" + extra, flush=True)
    json.dump(out, open(Path(__file__).parent / f"regime_gate_{TAG}.json", "w"))
    print("BITTI", flush=True)
