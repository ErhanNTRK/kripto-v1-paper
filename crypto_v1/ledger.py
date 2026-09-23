"""Trade ledger: every Futures buy and sell the bot (or the user, by hand)
made, grouped into round trips with their real USDT result.

The user's request, 24 Sep 2026: keep every trade on record, with profit in
USDT, ready to hand over at any time. Binance is the source of truth -- each
fill's price, quantity, realized P&L and commission (/fapi/v1/userTrades)
and the funding fees (/fapi/v1/income) -- so nothing depends on the bot
having been running when a trade happened, and positions closed by hand are
included too. Fills are stored incrementally in the ledger folder (Binance
serves userTrades in 7-day windows only), so the history keeps growing past
what Binance itself still returns.
"""
import csv
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Futures trading started 21 Sep 2026; nothing earlier to collect.
LEDGER_START_MS = int(datetime(2026, 9, 20, tzinfo=timezone.utc).timestamp() * 1000)
WEEK_MS = 7 * 86_400_000
TR = timezone(timedelta(hours=3))

CLOSE_KIND = {"kv1fq": "stop", "kv1fp": "stop", "kv1fy": "cikis kurali", "kv1fx": "cikis kurali",
              "kv1fk": "acil kapatma", "kv1fe": "acil kapatma"}
OPEN_PREFIXES = ("kv1fl", "kv1fs")


def default_dir():
    return Path(os.environ.get("KRIPTO_LEDGER_DIR") or Path.home() / "kripto" / "islem-kayitlari")


def _load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"fills": {}, "clients": {}, "income": {}, "synced_to": LEDGER_START_MS}


def sync(executor, folder, now_ms=None):
    """Pull everything new since the last sync (with an hour of overlap;
    fills and income events are keyed by id, so re-reading is harmless)."""
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    store_path = folder / "binance-kayitlari.json"
    store = _load(store_path)
    now_ms = now_ms or int(time.time() * 1000)
    since = max(LEDGER_START_MS, int(store.get("synced_to", LEDGER_START_MS)) - 3_600_000)

    cursor = since
    while cursor < now_ms:
        batch = executor.income(cursor, min(now_ms, cursor + WEEK_MS))
        for item in batch:
            key = f"{item.get('incomeType')}:{item.get('tranId')}:{item.get('tradeId')}:{item.get('time')}"
            store["income"][key] = item
        if len(batch) >= 1000:
            cursor = int(batch[-1]["time"]) + 1
        else:
            cursor = min(now_ms, cursor + WEEK_MS)

    symbols = {i["symbol"] for i in store["income"].values()
               if i.get("symbol") and int(i.get("time", 0)) >= since}
    symbols |= {p["symbol"] for p in executor.position_risk() if float(p.get("positionAmt", "0"))}
    for symbol in sorted(symbols):
        start = since
        while start < now_ms:
            end = min(now_ms, start + WEEK_MS)
            batch = executor.user_trades(symbol, start, end)
            for fill in batch:
                store["fills"][str(fill["id"])] = fill
            start = int(batch[-1]["time"]) + 1 if len(batch) >= 1000 else end
        for order in executor.all_orders(symbol, start_time=since):
            store["clients"][str(order["orderId"])] = order.get("clientOrderId", "")
    store["synced_to"] = now_ms
    tmp = store_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(store), encoding="utf-8")
    tmp.replace(store_path)
    return store


def round_trips(store):
    """Fills -> round trips: a trip opens when a symbol's position leaves
    zero and closes when it returns to zero. Returns (closed, still_open)."""
    by_symbol = {}
    for fill in store["fills"].values():
        by_symbol.setdefault(fill["symbol"], []).append(fill)
    funding = [i for i in store["income"].values() if i.get("incomeType") == "FUNDING_FEE"]
    closed, still_open = [], []
    for symbol, fills in by_symbol.items():
        fills.sort(key=lambda f: (int(f["time"]), int(f["id"])))
        position, trip = 0.0, None
        for fill in fills:
            qty = float(fill["qty"]); signed = qty if fill["side"] == "BUY" else -qty
            client = store["clients"].get(str(fill["orderId"]), "")
            if trip is None:
                trip = {"symbol": symbol, "side": "long" if signed > 0 else "short",
                        "opened": int(fill["time"]), "opened_by": client,
                        "entry_qty": 0.0, "entry_cost": 0.0, "exit_qty": 0.0, "exit_value": 0.0,
                        "realized": 0.0, "commission": 0.0, "closed_by": ""}
            opening = (signed > 0) == (trip["side"] == "long")
            if opening:
                trip["entry_qty"] += qty; trip["entry_cost"] += qty * float(fill["price"])
            else:
                trip["exit_qty"] += qty; trip["exit_value"] += qty * float(fill["price"])
                trip["closed_by"] = client
            trip["realized"] += float(fill.get("realizedPnl", 0))
            trip["commission"] += float(fill.get("commission", 0))
            position += signed
            if abs(position) <= 1e-9 * max(1.0, trip["entry_qty"]):
                trip["closed"] = int(fill["time"])
                trip["funding"] = sum(float(i["income"]) for i in funding if i.get("symbol") == symbol
                                      and trip["opened"] <= int(i["time"]) <= trip["closed"])
                closed.append(trip); trip, position = None, 0.0
        if trip is not None:
            trip["funding"] = sum(float(i["income"]) for i in funding if i.get("symbol") == symbol
                                  and int(i["time"]) >= trip["opened"])
            still_open.append(trip)
    closed.sort(key=lambda t: t["closed"])
    return closed, still_open


def _system(client):
    if client.startswith(OPEN_PREFIXES) and len(client) > 5:
        return {"4": "4H", "2": "2H"}.get(client[5], "?")
    return "elle"


def _kind(client):
    return CLOSE_KIND.get(client[:5], "elle") if client else "elle"


def _when(ms):
    return datetime.fromtimestamp(ms / 1000, TR).strftime("%Y-%m-%d %H:%M")


def write_reports(store, folder, open_marks=None):
    """islemler.csv (one row per closed trip, Excel-friendly) and ozet.txt."""
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    closed, still_open = round_trips(store)
    rows = []
    for t in closed:
        net = t["realized"] - t["commission"] + t["funding"]
        rows.append({
            "kapanis": _when(t["closed"]), "acilis": _when(t["opened"]), "coin": t["symbol"],
            "sistem": _system(t["opened_by"]), "yon": t["side"], "miktar": round(t["entry_qty"], 8),
            "giris_fiyati": round(t["entry_cost"] / t["entry_qty"], 8) if t["entry_qty"] else "",
            "cikis_fiyati": round(t["exit_value"] / t["exit_qty"], 8) if t["exit_qty"] else "",
            "pozisyon_usdt": round(t["entry_cost"], 4), "kapanis_turu": _kind(t["closed_by"]),
            "brut_kz_usdt": round(t["realized"], 4), "komisyon_usdt": round(t["commission"], 4),
            "fonlama_usdt": round(t["funding"], 4), "net_kz_usdt": round(net, 4),
            "sure_saat": round((t["closed"] - t["opened"]) / 3_600_000, 1)})
    with (folder / "islemler.csv").open("w", newline="", encoding="utf-8-sig") as f:
        fields = list(rows[0]) if rows else ["kapanis", "coin", "net_kz_usdt"]
        writer = csv.DictWriter(f, fieldnames=fields, delimiter=";")
        writer.writeheader(); writer.writerows(rows)
    wins = [r for r in rows if r["net_kz_usdt"] > 0]
    total = sum(r["net_kz_usdt"] for r in rows)
    lines = [f"Kripto bot islem ozeti -- {_when(int(time.time() * 1000))} (TR saati)", "",
             f"Kapanmis islem: {len(rows)} | kazanan {len(wins)} | kaybeden {len(rows) - len(wins)}",
             f"Toplam net K/Z: {total:+.4f} USDT (komisyon ve fonlama dusulmus)",
             f"  brut {sum(r['brut_kz_usdt'] for r in rows):+.4f} | komisyon -{sum(r['komisyon_usdt'] for r in rows):.4f}"
             f" | fonlama {sum(r['fonlama_usdt'] for r in rows):+.4f}", ""]
    for system in ("4H", "2H", "elle"):
        part = [r for r in rows if r["sistem"] == system]
        if part:
            lines.append(f"  {system}: {len(part)} islem, net {sum(r['net_kz_usdt'] for r in part):+.4f} USDT, "
                         f"kazanan {sum(1 for r in part if r['net_kz_usdt'] > 0)}")
    lines += ["", f"Acik pozisyon: {len(still_open)}"]
    for t in still_open:
        mark = (open_marks or {}).get(t["symbol"])
        line = f"  {t['symbol']} ({_system(t['opened_by'])}, {t['side']}) acilis {_when(t['opened'])}"
        if mark is not None:
            line += f" | gerceklesmemis {mark:+.4f} USDT"
        lines.append(line)
    lines += ["", "Tum islemler: islemler.csv (Excel ile acilir)"]
    (folder / "ozet.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return rows, still_open


def refresh(executor, folder=None):
    folder = folder or default_dir()
    store = sync(executor, folder)
    marks = {p["symbol"]: float(p.get("unRealizedProfit", 0)) for p in executor.position_risk()
             if float(p.get("positionAmt", "0"))}
    return write_reports(store, folder, marks)
