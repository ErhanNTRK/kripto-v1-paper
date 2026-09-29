"""How much of the pyramid result is ordering luck? pend_add / pend_sell are Python sets of symbol strings, so their
iteration order (and thus which add gets the last margin) changes with PYTHONHASHSEED. Run full-path R_2_4 and
no-pyramid under several seeds (the caller sets PYTHONHASHSEED per process)."""
import os, sys
sys.argv = ["x"]
os.environ.setdefault("PIT_DIR", r"C:\Users\ASUS-PC\kripto\arastirma-verisi\pit")
import importlib.util
from pathlib import Path
src = Path(__file__).with_name("pit_pyramid.py").read_text(encoding="utf-8")
src = src.split("ts = lambda y, m:")[0]          # precompute + run() only
ns = {"__file__": str(Path(__file__).with_name("pit_pyramid.py"))}
exec(compile(src, "pit_pyramid_core", "exec"), ns)
for k in ("yok", "R_2_4", "R_tam_4"):
    c, tr = ns["run"](ns["V"][k])
    print(f"seed {os.environ.get('PYTHONHASHSEED')} {k:8} {c[-1][1]/170:6.2f}x", flush=True)
