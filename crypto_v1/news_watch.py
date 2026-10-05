"""News watch (user's request, 5 Oct 2026: "can we hear about a Fed rate move or news as it happens?").
Telegram messages only -- it never places an order. Own thread, every 5 minutes:

  [FED]          a new Federal Reserve press release in the "Monetary Policy" category or naming the FOMC (rate
                 decisions, statements, minutes), with BTC's price; 15 minutes later BTC's reaction
  [FED-TAKVIM]   the day before an FOMC decision (dates read from the Fed's own calendar page)
  [HABER]        a crypto headline (Cointelegraph, CoinDesk) with a market-moving keyword; at most 8 a day
  [HAREKET]      BTC moved 3% or more within the last hour, with the latest headlines; then quiet for 2 hours

The first pass only marks what is already in the feeds as seen, so a restart never floods the chat.
Headlines are sent in their original English."""
import json
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

FED_FEED = "https://www.federalreserve.gov/feeds/press_all.xml"
FOMC_PAGE = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
CRYPTO_FEEDS = {"cointelegraph": "https://cointelegraph.com/rss",
                "coindesk": "https://www.coindesk.com/arc/outboundfeeds/rss/"}
KEYWORDS = ("etf", " sec ", "hack", "exploit", "binance", "delist", " ban ", " bans ", " fed ", "fomc", "powell", "rate cut",
            "rate hike", "interest rate", "cpi", "inflation", "tariff", "liquidat", "stablecoin", "sanction",
            "blackrock", "treasury")
TR = timezone(timedelta(hours=3))
H, MIN = 3_600_000, 60_000
DAILY_NEWS_LIMIT, MOVE, MOVE_QUIET = 8, 0.03, 2 * H
MONTHS = ("January February March April May June July August September October November December").split()


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (kripto-bot news watch)"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8", errors="ignore")


def btc_hour():
    """(price now, change over the last hour) from public 5m futures bars."""
    with urllib.request.urlopen("https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=5m&limit=13",
                                timeout=20) as r:
        k = json.loads(r.read())
    return float(k[-1][4]), float(k[-1][4]) / float(k[0][4]) - 1


def parse_rss(text):
    """[(id, title, link, category)] newest first."""
    root = ET.fromstring(text.lstrip("﻿").strip())
    items = []
    for item in root.iter("item"):
        get = lambda tag: (item.findtext(tag) or "").strip()
        items.append((get("guid") or get("link"), get("title"), get("link"), get("category")))
    return items


def fomc_dates(html):
    """Decision days (the second day of each meeting) as 'YYYY-MM-DD', from the Fed's calendar page."""
    out = []
    for m in re.finditer(r"(\d{4}) FOMC Meetings", html):
        year, seg = int(m.group(1)), html[m.end():m.end() + 20000]
        nxt = re.search(r"\d{4} FOMC Meetings", seg)
        text = re.sub(r"<[^>]+>", " ", seg[:nxt.start()] if nxt else seg)
        for mm in re.finditer(r"(" + "|".join(MONTHS) + r")(?:/(" + "|".join(MONTHS) + r"))?\s+(\d{1,2})-(\d{1,2})", text):
            month = MONTHS.index(mm.group(2) or mm.group(1)) + 1
            out.append(f"{year}-{month:02d}-{int(mm.group(4)):02d}")
    return sorted(set(out))


class NewsWatch:
    def __init__(self, state_path, fetch=fetch, btc=btc_hour, send=None, clock=time.time):
        self.path, self.fetch, self.btc, self.clock = state_path, fetch, btc, clock
        if send is None:
            from .telegram import send_message as send
        self.send = send

    def _state(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save(self, state):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(state, f)

    def _say(self, text):
        try:
            self.send(text)
        except Exception as exc:
            print(f"News watch Telegram failed: {exc}", flush=True)

    def _new_items(self, state, name, url):
        """Items not seen before; on a feed's first read everything counts as seen."""
        try:
            items = parse_rss(self.fetch(url))
        except Exception as exc:
            print(f"News watch: {name} unreadable ({exc})", flush=True)
            return []
        seen = state.setdefault("seen", {}).setdefault(name, [])
        fresh = [i for i in items if i[0] and i[0] not in seen]
        first = name not in state.setdefault("ready", [])
        state["seen"][name] = (seen + [i[0] for i in fresh])[-500:]
        if first:
            state["ready"].append(name)
            return []
        return fresh

    def tick(self):
        now = int(self.clock() * 1000)
        state = self._state()
        today = datetime.fromtimestamp(now / 1000, TR).strftime("%Y-%m-%d")
        if state.get("day") != today:
            state.update(day=today, sent=0)
        try:
            price, change = self.btc()
        except Exception as exc:
            print(f"News watch: BTC price unreadable ({exc})", flush=True)
            price, change = None, 0.0

        for _, title, link, category in self._new_items(state, "fed", FED_FEED):
            if category == "Monetary Policy" or "FOMC" in title:
                tail = f"\nBTC su an {price:,.0f}. 15 dakika sonra tepkisini yazacagim." if price else ""
                self._say(f"[FED] {title}\n{link}{tail}")
                if price:
                    state.setdefault("pending", []).append({"t": now, "btc": price, "title": title[:80]})
        for p in list(state.get("pending", [])):
            if price and now - p["t"] >= 15 * MIN:
                self._say(f"[FED] 15 dakikalik tepki ({p['title']}): BTC {p['btc']:,.0f} -> {price:,.0f} "
                          f"(%{100 * (price / p['btc'] - 1):+.2f})")
                state["pending"].remove(p)

        recent = state.setdefault("recent", [])
        for name, url in CRYPTO_FEEDS.items():
            for _, title, link, _ in self._new_items(state, name, url):
                recent.insert(0, title)
                low = " " + re.sub(r"[^a-z0-9]+", " ", title.lower()) + " "   # "SEC's" -> " sec s "
                if any(k in low for k in KEYWORDS) and state["sent"] < DAILY_NEWS_LIMIT:
                    self._say(f"[HABER] {title}\n{link}")
                    state["sent"] += 1
        state["recent"] = recent[:10]

        if price and abs(change) >= MOVE and now - state.get("move_alert", 0) > MOVE_QUIET:
            heads = "\n- ".join(state["recent"][:3]) or "akista yeni baslik yok"
            self._say(f"[HAREKET] BTC son 1 saatte %{100 * change:+.1f} ({price:,.0f}). Son basliklar:\n- {heads}")
            state["move_alert"] = now

        if state.get("fomc_day") != today:
            try:
                state["fomc"] = fomc_dates(self.fetch(FOMC_PAGE)); state["fomc_day"] = today
            except Exception as exc:
                print(f"News watch: FOMC calendar unreadable ({exc})", flush=True)
        tomorrow = (datetime.fromtimestamp(now / 1000, TR) + timedelta(days=1)).strftime("%Y-%m-%d")
        if tomorrow in state.get("fomc", []) and tomorrow not in state.setdefault("reminded", []):
            self._say(f"[FED-TAKVIM] Yarin ({tomorrow}) FOMC faiz karari aciklanacak: TR saatiyle 21:00-22:00 civari "
                      f"(14:00 ABD Dogu). Karar aninda [FED] mesaji gelecek; o saatlerde fiyat sert oynayabilir.")
            state["reminded"].append(tomorrow)
        self._save(state)
        return state


def run_forever(state_path, every_s=300, sleep=time.sleep):
    watch = NewsWatch(state_path)
    while True:
        try:
            watch.tick()
        except Exception as exc:  # a network error must never kill the thread
            print(f"News watch failed: {exc}", flush=True)
        sleep(every_s)
