import os
import tempfile
import unittest

from crypto_v1 import news_watch as nw

H = 3_600_000


def rss(items):
    body = "".join(f"<item><title>{t}</title><link>https://x/{i}</link><guid>g{i}</guid><category>{c}</category></item>"
                   for i, t, c in items)
    return f"﻿<?xml version='1.0'?><rss><channel>{body}</channel></rss>"


CAL = ("<h4>2026 FOMC Meetings</h4><div>October</div><div>27-28</div><div>December</div><div>8-9*</div>"
       "<h4>2025 FOMC Meetings</h4><div>April/May</div><div>30-1</div>")


class Feeds:
    def __init__(self):
        self.fed, self.ct, self.cd = [], [], []

    def __call__(self, url):
        if url == nw.FED_FEED: return rss(self.fed)
        if url == nw.FOMC_PAGE: return CAL
        if "cointelegraph" in url: return rss(self.ct)
        return rss(self.cd)


class NewsWatchTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.now = 1_792_900_000_000          # 2026-10-25 TR: two days before the decision
        self.feeds, self.sent, self.move = Feeds(), [], (86000.0, 0.0)
        self.watch = nw.NewsWatch(os.path.join(self.dir.name, "news.json"), fetch=self.feeds,
                                  btc=lambda: self.move, send=self.sent.append, clock=lambda: self.now / 1000,
                                  translate=lambda text: "TR: " + text)

    def tearDown(self):
        self.dir.cleanup()

    def test_first_pass_marks_everything_seen(self):
        self.feeds.fed = [(1, "Federal Reserve issues FOMC statement", "Monetary Policy")]
        self.feeds.ct = [(2, "SEC approves a bitcoin ETF", "")]
        self.watch.tick()
        self.assertEqual([m for m in self.sent if not m.startswith("[FED-TAKVIM]")], [])

    def test_fed_release_and_reaction(self):
        self.watch.tick()
        self.feeds.fed = [(5, "Federal Reserve issues FOMC statement", "Monetary Policy"),
                          (6, "Federal Reserve Board announces approval of application", "Orders on Banking Applications")]
        self.watch.tick()
        fed = [m for m in self.sent if m.startswith("[FED] ")]
        self.assertEqual(len(fed), 1)
        self.assertIn("FOMC statement", fed[0])
        self.now += 16 * 60_000; self.move = (87720.0, 0.02)
        self.watch.tick()
        self.assertIn("+2.00", self.sent[-1])

    def test_keyword_filter_and_word_edges(self):
        self.watch.tick()
        self.feeds.ct = [(7, "Bankers keep dealmaking", ""), (8, "SEC's new rule hits exchanges", ""),
                         (9, "Weekly recap of memes", "")]
        self.watch.tick()
        news = [m for m in self.sent if m.startswith("[HABER]")]
        self.assertEqual(len(news), 1)
        self.assertIn("SEC's", news[0])
        self.assertTrue(news[0].startswith("[HABER] TR: SEC's"))
        self.assertIn("(EN: SEC's new rule hits exchanges)", news[0])

    def test_btc_move_alert_then_quiet(self):
        self.watch.tick()
        self.move = (83000.0, -0.035)
        self.watch.tick(); self.now += H; self.watch.tick()
        self.assertEqual(sum(m.startswith("[HAREKET]") for m in self.sent), 1)

    def test_untranslated_headline_still_sent(self):
        self.watch.translate = lambda text: None
        self.watch.tick()
        self.feeds.cd = [(11, "Binance lists a new coin", "")]
        self.watch.tick()
        news = [m for m in self.sent if m.startswith("[HABER]")]
        self.assertEqual(news, ["[HABER] Binance lists a new coin" + chr(10) + "https://x/11"])

    def test_fomc_calendar(self):
        self.assertEqual(nw.fomc_dates(CAL), ["2025-05-01", "2026-10-28", "2026-12-09"])

    def test_reminder_the_day_before(self):
        self.now = 1_793_080_000_000           # 2026-10-27 TR
        self.watch.tick(); self.watch.tick()
        reminders = [m for m in self.sent if m.startswith("[FED-TAKVIM]")]
        self.assertEqual(len(reminders), 1)
        self.assertIn("2026-10-28", reminders[0])


if __name__ == "__main__":
    unittest.main()
