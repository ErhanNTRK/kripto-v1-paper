import unittest

from crypto_v1.bekci import DOWN_ALERT_SECONDS, REMIND_SECONDS, check


def healthy(now):
    def fetch(url):
        if url.endswith("/health"):
            return {"ready": True}
        return {"apps": {"4": {"at": now - 60}, "2": {"at": now - 30}}}
    return fetch


def down(url):
    raise OSError("connection refused")


class BekciTests(unittest.TestCase):
    """The outside watchdog (audit C2): the bot cannot report itself gone."""

    def setUp(self):
        self.sent = []

    def _check(self, state, fetch, now):
        return check(state, fetch, self.sent.append, now)

    def test_a_healthy_bot_needs_no_message(self):
        self.assertEqual(self._check({}, healthy(1000), 1000), {})
        self.assertEqual(self.sent, [])

    def test_a_short_outage_is_not_reported(self):
        # The launcher restarts a crashed bot in ~15 s; a deploy takes a minute.
        state = self._check({}, down, 1000)
        state = self._check(state, down, 1000 + DOWN_ALERT_SECONDS - 1)
        self.assertEqual(self.sent, [])
        self.assertEqual(self._check(state, healthy(1400), 1400 + DOWN_ALERT_SECONDS), {})
        self.assertEqual(self.sent, [])

    def test_ten_minutes_down_is_reported_then_every_six_hours_then_the_recovery(self):
        state = self._check({}, down, 0)
        state = self._check(state, down, DOWN_ALERT_SECONDS)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("10 dakikadir cevap vermiyor", self.sent[0])
        self.assertEqual(REMIND_SECONDS, 6 * 3600)
        state = self._check(state, down, DOWN_ALERT_SECONDS + 5 * 3600)
        self.assertEqual(len(self.sent), 1)
        state = self._check(state, down, DOWN_ALERT_SECONDS + REMIND_SECONDS)
        self.assertEqual(len(self.sent), 2)
        now = DOWN_ALERT_SECONDS + REMIND_SECONDS + 300
        self.assertEqual(self._check(state, healthy(now), now), {})
        self.assertIn("yeniden calisiyor", self.sent[-1])

    def test_a_loop_that_stopped_ticking_counts_as_down(self):
        def fetch(url):
            return {"ready": True} if url.endswith("/health") else {"apps": {"4": {"at": 100}}}
        state = self._check({}, fetch, 3000)
        self._check(state, fetch, 3000 + DOWN_ALERT_SECONDS)
        self.assertIn("islem turu yapmiyor", self.sent[0])

    def test_a_just_started_bot_without_ticks_is_fine(self):
        def fetch(url):
            return {"ready": True} if url.endswith("/health") else {"apps": {"4": None, "2": None}}
        self.assertEqual(self._check({}, fetch, 1000), {})

    def test_a_failed_telegram_send_is_retried_next_run(self):
        def failing(text):
            raise OSError("offline")
        state = check({"down_since": 0}, down, failing, DOWN_ALERT_SECONDS)
        self.assertFalse(state["alerted"])
        self._check(state, down, DOWN_ALERT_SECONDS + 300)
        self.assertEqual(len(self.sent), 1)


if __name__ == "__main__":
    unittest.main()
