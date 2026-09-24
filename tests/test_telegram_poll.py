import unittest
import unittest.mock
from unittest.mock import MagicMock

from crypto_v1.telegram_poll import TelegramApprovals, parse_command, symbol_of


def update(text, chat=123, kind="private", update_id=1):
    return {"update_id": update_id, "message": {"text": text, "chat": {"id": chat, "type": kind}}}


class FakeApp:
    def __init__(self, tag, waiting=()):
        self.tag, self.waiting, self.approved = tag, set(waiting), []

    def awaiting_approval(self, now_ms=None):
        return sorted(self.waiting)

    def approve(self, symbol, now_ms=None):
        if symbol in self.waiting:
            self.approved.append(symbol)
            return True
        return False


class ParsingTests(unittest.TestCase):
    def test_only_the_configured_private_chat_counts(self):
        self.assertEqual(parse_command(update(" AL NIL "), "123"), "AL NIL")
        self.assertIsNone(parse_command(update("AL NIL", chat=999), "123"))
        self.assertIsNone(parse_command(update("AL NIL", kind="group"), "123"))
        self.assertIsNone(parse_command(update("  "), "123"))

    def test_a_bare_coin_name_gets_its_usdt_pair(self):
        self.assertEqual(symbol_of("nil"), "NILUSDT")
        self.assertEqual(symbol_of("NILUSDT"), "NILUSDT")


class ApprovalCommandTests(unittest.TestCase):
    def _poller(self, *apps):
        send = MagicMock()
        return TelegramApprovals("token", "123", list(apps), send=send), send

    def test_al_coin_approves_the_system_that_asked(self):
        four, two = FakeApp("4", {"NILUSDT"}), FakeApp("2")
        poller, send = self._poller(four, two)
        self.assertEqual(poller.handle(update("al nil")), "approved")
        self.assertEqual(four.approved, ["NILUSDT"])
        self.assertIn("Onay alindi: NILUSDT (4H)", send.call_args.args[0])

    def test_a_coin_nobody_asked_about_is_refused(self):
        poller, send = self._poller(FakeApp("4"))
        self.assertEqual(poller.handle(update("AL SAGA")), "none")
        self.assertIn("bekleyen bir onay yok", send.call_args.args[0])

    def test_bare_al_works_only_when_exactly_one_coin_waits(self):
        four = FakeApp("4", {"NILUSDT"})
        poller, _ = self._poller(four, FakeApp("2"))
        self.assertEqual(poller.handle(update("AL")), "approved")
        both = FakeApp("4", {"NILUSDT", "SAGAUSDT"})
        poller, send = self._poller(both)
        self.assertEqual(poller.handle(update("AL")), "ambiguous")
        self.assertEqual(both.approved, [])
        self.assertIn("NILUSDT, SAGAUSDT", send.call_args.args[0])

    def test_other_text_gets_the_help_line(self):
        poller, send = self._poller(FakeApp("4", {"NILUSDT"}))
        self.assertEqual(poller.handle(update("merhaba")), "help")
        self.assertIn("AL <COIN>", send.call_args.args[0])

    def test_a_stranger_is_ignored_silently(self):
        four = FakeApp("4", {"NILUSDT"})
        poller, send = self._poller(four)
        self.assertIsNone(poller.handle(update("AL NIL", chat=999)))
        send.assert_not_called()
        self.assertEqual(four.approved, [])


class PollingLoopTests(unittest.TestCase):
    def test_old_messages_are_skipped_and_new_ones_handled(self):
        four = FakeApp("4", {"NILUSDT"})
        poller = TelegramApprovals("token", "123", [four], send=MagicMock(), sleep=lambda s: None)
        calls = []

        def fake_updates(timeout):
            calls.append(timeout)
            if timeout == 0:
                return [update("AL NIL", update_id=5)]  # sent while the bot was down
            return [update("AL NIL", update_id=6)]
        poller._updates = fake_updates
        poller.run(iterations=1)
        self.assertEqual(calls, [0, poller.POLL_SECONDS])
        self.assertEqual(four.approved, ["NILUSDT"])  # only the new one
        self.assertEqual(poller.offset, 7)

    def test_a_refused_poll_is_reported_once_and_retried(self):
        poller = TelegramApprovals("token", "123", [], send=MagicMock(), sleep=lambda s: None)
        poller._updates = MagicMock(side_effect=RuntimeError("Telegram getUpdates failed: HTTP 409"))
        with unittest.mock.patch("builtins.print") as out:
            poller.run(iterations=3)
        self.assertEqual(out.call_count, 1)
        self.assertIn("HTTP 409", out.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
