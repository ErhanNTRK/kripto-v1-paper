"""Read the user's Telegram replies by polling (the PC has no public address
for a webhook) and turn "AL <COIN>" into an approval for a coin outside the
automatic list (user's decision, 24 Sep 2026: the top 30 by 7-day volume are
bought automatically, the rest only after the user says so)."""
import json
import time
import urllib.error
import urllib.request

from .telegram import send_message

HELP = ("Komut: AL <COIN> (ornek: AL NIL). Yalnizca bot 'ONAY GEREKIYOR' diye "
        "sorduktan sonra ve sure dolmadan gecerlidir.")


def parse_command(update, expected_chat):
    """The text of a message from the one configured private chat, else None."""
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    if chat.get("type") != "private" or str(chat.get("id", "")) != str(expected_chat):
        return None
    text = str(message.get("text", "")).strip()
    return text or None


def symbol_of(word):
    word = word.strip().upper()
    return word if word.endswith("USDT") else word + "USDT"


class TelegramApprovals:
    POLL_SECONDS = 25

    def __init__(self, token, chat_id, apps, send=send_message, opener=urllib.request.urlopen,
                 sleep=time.sleep):
        self.token, self.chat_id, self.apps = token, str(chat_id), apps
        self.send, self.opener, self.sleep = send, opener, sleep
        self.offset = None
        self._conflict_reported = False

    def _call(self, method, params, timeout):
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{self.token}/{method}",
            data=json.dumps(params).encode("utf-8"), headers={"Content-Type": "application/json"})
        try:
            with self.opener(request, timeout=timeout) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as error:
            # Never print the error itself: its URL carries the bot token.
            raise RuntimeError(f"Telegram {method} failed: HTTP {error.code}") from None
        except Exception as error:
            raise RuntimeError(f"Telegram {method} failed: {type(error).__name__}") from None
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram {method} refused")
        return payload.get("result", [])

    def _updates(self, timeout):
        params = {"timeout": timeout, "allowed_updates": ["message"]}
        if self.offset is not None:
            params["offset"] = self.offset
        return self._call("getUpdates", params, timeout + 10)

    def skip_backlog(self):
        """Messages sent while the bot was down answer questions that no
        longer exist; start after them."""
        for update in self._updates(0):
            self.offset = int(update["update_id"]) + 1

    def handle(self, update):
        text = parse_command(update, self.chat_id)
        if text is None:
            return None
        words = text.split()
        if words[0].upper() != "AL":
            self.send(HELP)
            return "help"
        now_ms = int(time.time() * 1000)
        if len(words) == 1:
            waiting = sorted({s for app in self.apps for s in app.awaiting_approval(now_ms)})
            if len(waiting) != 1:
                self.send("Onay bekleyen coin yok." if not waiting else
                          "Birden fazla coin onay bekliyor: " + ", ".join(waiting) + ". Hangisi? Ornek: AL "
                          + waiting[0].removesuffix("USDT"))
                return "ambiguous"
            symbol = waiting[0]
        else:
            symbol = symbol_of(words[1])
        approved = [app.tag or "?" for app in self.apps if app.approve(symbol, now_ms)]
        if approved:
            self.send(f"Onay alindi: {symbol} ({', '.join(t + 'H' for t in approved)}). "
                      "En gec birkac dakika icinde alinacak; fiyat izin verilen araliktan cikmissa alinmaz.")
            return "approved"
        self.send(f"{symbol} icin bekleyen bir onay yok (suresi dolmus olabilir).")
        return "none"

    def run(self, iterations=None):
        done = 0
        while iterations is None or done < iterations:
            done += 1
            try:
                if self.offset is None:
                    self.skip_backlog()
                    if self.offset is None:
                        self.offset = 0
                for update in self._updates(self.POLL_SECONDS):
                    self.offset = int(update["update_id"]) + 1
                    try:
                        self.handle(update)
                    except Exception as exc:
                        print(f"Telegram command failed: {exc}", flush=True)
                self._conflict_reported = False
            except Exception as exc:
                # HTTP 409: a webhook is still registered for this bot, so
                # Telegram refuses polling. Said once, then retried quietly.
                if not self._conflict_reported:
                    print(f"Telegram polling unavailable: {exc}", flush=True)
                    self._conflict_reported = True
                self.sleep(60)
