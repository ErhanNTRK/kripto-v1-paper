"""Is the blind-test loss a cost artefact? Sim assumes fee 0.1%/side + 0.05% slippage; real Binance taker is
~0.05%/side and measured live entry slippage was about -0.07% (better). Re-run the live rules on the dataset
given by PIT_DIR with real costs. Usage: PIT_DIR=... python pit_costs.py"""
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import pit_sim2 as Q
P = Q.P
def dd(c):
    pk, m = None, 0.0
    for _, e in c:
        pk = e if pk is None else max(pk, e); m = max(m, 1 - e / pk)
    return m
for label, fee, slip in (("simdiki varsayim (0.10% + 0.05%)", 0.001, 0.0005),
                         ("gercekci (0.05% + 0.02%)", 0.0005, 0.0002),
                         ("maliyet yok (ust sinir)", 0.0, 0.0)):
    P.FEE, P.SLIP = fee, slip
    for risk in (0.01, 0.005):
        r = Q.run2(label, momentum=False, trail={"4H": 6.0, "2H": 8.0}, risk=risk)
        c = r["curve"]
        print(f"{os.environ.get('PIT_DIR','pit')[-7:]:8} {label:34} risk {risk:.3f}: {c[-1][1]/170:6.2f}x dusus %{100*dd(c):3.0f}", flush=True)
print("BITTI", flush=True)
