"""Meta-labeling: a model that grades the RULE SYSTEM's own buy signals.
Every signal the live rules would fire (both systems, no capacity limit) is
followed with the live exit (2-ATR stop, trailing after 1R, channel/BTC
exit) to get its real R. Features = ml_rank's ~41 features at the signal
bar. Monthly walk-forward (train only on signals already CLOSED before the
month). Then a portfolio run (backtest engine, real capacity) skipping the
signals the model grades lowest, vs the live rules, on the same
out-of-sample window. Cached data only; research venv."""
import json, sys, datetime, time
from pathlib import Path
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier

REPO = "C:/Users/ASUS-PC/Documents/GitHub/kripto-v1-paper"; sys.path.insert(0, REPO)
sys.path.insert(0, str(Path(__file__).parent))
import crypto_v1.research_v5 as rv5
import crypto_v1.backtest as bt
from crypto_v1.risk import validate_config
import ml_rank_features as mf

CACHE = Path("C:/Users/ASUS-PC/kripto/arastirma-verisi")
meta = json.loads((CACHE / "meta.json").read_text(encoding="utf-8"))
symbols, start, end = meta["symbols"], meta["test_start"], meta["end"]
DAY = 86_400_000; H4, H2 = 4 * 3600 * 1000, 2 * 3600 * 1000
cfg4 = validate_config(json.loads(Path(REPO, "config_v5_long.json").read_text(encoding="utf-8")))
cfg2 = validate_config(json.loads(Path(REPO, "config_v5_long_2h.json").read_text(encoding="utf-8")))
FEE, SLIP = cfg4["fee"], cfg4["slippage"]


def follow(rows, feats, btc_feats, i, cfg):
    """R of a long signalled at bar i, with the live exit rules."""
    f = feats[i]
    if i + 1 >= len(rows): return None, None
    entry = rows[i + 1]["o"] * (1 + SLIP)
    stop = f["c"] - cfg["atr_multiplier"] * f["atr"]
    if stop <= 0 or entry <= stop: return None, None
    unit = entry * (1 + FEE) - stop * (1 - SLIP) * (1 - FEE)
    high = entry; pending_exit = False
    for j in range(i + 1, min(len(rows), i + 1 + 600)):
        r = rows[j]
        if pending_exit:
            px = r["o"]; return (px * (1 - SLIP) * (1 - FEE) - entry * (1 + FEE)) / unit, r["t"]
        if r["l"] <= stop:
            px = min(r["o"], stop); return (px * (1 - SLIP) * (1 - FEE) - entry * (1 + FEE)) / unit, r["t"]
        fj = feats[j]; b = btc_feats.get(r["t"])
        if b is None or rv5.long_exit(fj, b, cfg.get("btc_filter", "strict")):
            pending_exit = True
        high = max(high, r["h"])
        if high >= entry + unit:
            stop = max(stop, high - cfg["trailing_atr"] * fj["atr"])
    return None, None


t0 = time.time()
records = []   # (system, symbol, t, R, exit_t, features...)
names = None
for label, iv, fl, cfg in (("4H", H4, "4h", cfg4), ("2H", H2, "2h", cfg2)):
    data = json.loads((CACHE / f"data_{fl}.json").read_text(encoding="utf-8"))
    btc_rows = data["BTCUSDT"]
    btc_feats_list = rv5.symmetric_features(btc_rows, cfg)
    btc_feats = {r["t"]: r for r in btc_feats_list}
    btc_ml, _ = mf.features(btc_rows)
    btc_t = {r["t"]: k for k, r in enumerate(btc_rows)}
    bc = np.array([r["c"] for r in btc_rows]); per_day = DAY // iv
    ma200 = mf.rolling(bc, 200 * per_day, np.mean); e50 = mf.ema(bc, 50)
    for s in symbols:
        rows = data.get(s)
        if not rows or len(rows) < 300: continue
        feats = rv5.symmetric_features(rows, cfg)
        ml, _ = mf.features(rows)
        keys = sorted(ml)
        if names is None:
            names = keys + ["btc_r6", "btc_r42", "btc_ema50", "btc_200d", "rs42", "system_4h"]
        for i, f in enumerate(feats):
            if f["t"] < start or f["t"] >= end: continue
            b = btc_feats.get(f["t"])
            if b is None or rv5.long_entry(f, b, cfg) is None: continue
            R, exit_t = follow(rows, feats, btc_feats, i, cfg)
            if R is None: continue
            k = btc_t[f["t"]]
            x = [ml[key][i] for key in keys] + [btc_ml["r6"][k], btc_ml["r42"][k], bc[k] / e50[k] - 1,
                                                bc[k] / ma200[k] - 1 if not np.isnan(ma200[k]) else np.nan,
                                                ml["r42"][i] - btc_ml["r42"][k], 1.0 if label == "4H" else 0.0]
            records.append((label, s, f["t"], R, exit_t, x))
    print(f"{label}: sinyaller hazir ({sum(1 for r in records if r[0]==label)}) {time.time()-t0:.0f} sn", flush=True)

T = np.array([r[2] for r in records]); R = np.array([r[3] for r in records]); E = np.array([r[4] for r in records])
X = np.array([r[5] for r in records], dtype=float); Y = (R >= 1.0).astype(float)
print(f"toplam sinyal {len(R)}, ort R {R.mean():+.3f}, R>=1 orani %{100*Y.mean():.1f}", flush=True)

mk = lambda ms: datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc)
months = sorted({(mk(t).year, mk(t).month) for t in T})
first = (2024, 9)
score = np.full(len(R), np.nan); thr30 = {}; thr50 = {}
for ym in months:
    if ym < first: continue
    m0 = int(datetime.datetime(ym[0], ym[1], 1, tzinfo=datetime.timezone.utc).timestamp() * 1000)
    nx = (ym[0] + ym[1] // 12, ym[1] % 12 + 1)
    m1 = int(datetime.datetime(nx[0], nx[1], 1, tzinfo=datetime.timezone.utc).timestamp() * 1000)
    train = E < m0            # only signals whose outcome was already known
    test = (T >= m0) & (T < m1)
    if not test.any(): continue
    model = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, max_leaf_nodes=15,
                                           min_samples_leaf=50, l2_regularization=1.0, random_state=1)
    model.fit(X[train], Y[train])
    tr_pred = model.predict_proba(X[train])[:, 1]
    thr30[ym], thr50[ym] = np.quantile(tr_pred, 0.3), np.quantile(tr_pred, 0.5)
    score[test] = model.predict_proba(X[test])[:, 1]
oos = ~np.isnan(score)
print(f"\nORNEKLEM DISI sinyal {oos.sum()}: ort R {R[oos].mean():+.3f}")
qs = np.quantile(score[oos], [0, .2, .4, .6, .8, 1])
for q in range(5):
    m = oos & (score >= qs[q]) & ((score <= qs[q + 1]) if q == 4 else (score < qs[q + 1]))
    print(f"  puan dilimi {q+1} (dusukten yuksege): ort R {R[m].mean():+.3f}, R>=1 %{100*Y[m].mean():.0f}, toplam R {R[m].sum():+.0f} ({m.sum()})")

# portfolio run with the backtest engine, out-of-sample window only
score_map = {}
for k in np.where(oos)[0]:
    score_map[(records[k][0], records[k][1], int(T[k]))] = score[k]
LEVERAGE = 4
def leveraged_size(equity, cash, entry, stop, c):
    if stop <= 0 or entry <= stop: return None
    unit_risk = entry * (1 + c['fee']) - stop * (1 - c['slippage']) * (1 - c['fee'])
    qty = min(equity * c['risk_fraction'] / unit_risk, LEVERAGE * equity / (entry * (1 + c['fee'])))
    if qty * entry < 5: return None
    target = (entry * (1 + c['fee']) + c['min_reward_risk'] * unit_risk) / ((1 - c['slippage']) * (1 - c['fee']))
    return dict(qty=qty, entry=entry, stop=stop, target=target, initial_risk=unit_risk * qty, unit_risk=unit_risk, high=entry)
bt.size_position = leveraged_size
_fc = {}
def feats_cached(rows, config):
    if id(rows) not in _fc: _fc[id(rows)] = rv5.symmetric_features(rows, config)
    return _fc[id(rows)]
class Gate(rv5.ShortWindowLongModel):
    features = staticmethod(feats_cached)
    @staticmethod
    def buy(row, btc, config):
        if rv5.long_entry(row, btc, config) is None: return False
        mode = config.get("gate")
        if not mode: return True
        sc = score_map.get((config["label"], config["sym_of"].get(id(row)), row["t"]))
        if sc is None: return False
        d = mk(row["t"]); thr = (thr30 if mode == 30 else thr50).get((d.year, d.month))
        return thr is not None and sc >= thr
oos_start = int(datetime.datetime(2024, 9, 1, tzinfo=datetime.timezone.utc).timestamp() * 1000)
base = dict(risk_fraction=0.0075, initial_cash=50.0, daily_loss_fraction=0.10, max_consecutive_losses=100000)
for gate in (None, 30, 50):
    totals, trades = 0.0, 0
    for label, iv, fl, cfg in (("4H", H4, "4h", cfg4), ("2H", H2, "2h", cfg2)):
        data = json.loads((CACHE / f"data_{fl}.json").read_text(encoding="utf-8"))
        sym_of = {}
        for s, rows in data.items():
            for r in feats_cached(rows, cfg): sym_of[id(r)] = s
        c = dict(cfg, max_positions=6 if label == "4H" else 10, gate=gate, label=label, sym_of=sym_of, **base)
        st = bt.run(data, symbols, c, oos_start, end, Gate, iv)
        totals += st["curve"][-1]["equity"]; trades += len(st["trades"])
    weeks = (end - oos_start) / (7 * DAY)
    name = "kurallar (su anki)" if gate is None else f"model en dusuk %{gate}'u eler"
    print(f"PORTFOY Eyl 2024-Eyl 2026 | {name:28}: 100 -> {totals:.1f}, islem/hafta {trades/weeks:.1f}", flush=True)
print("BITTI", flush=True)
