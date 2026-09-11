import json
import os
import unittest
from unittest.mock import MagicMock, patch

from crypto_v1.github_worker import private_chat_id


class Response:
    def __init__(self, payload):
        self.payload = payload
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self):
        return json.dumps(self.payload).encode()


class GithubWorkerTests(unittest.TestCase):
    def test_resolves_exactly_one_private_start(self):
        payload = {"result": [{"message": {"text": "/start", "chat": {"id": 123, "type": "private"}}}]}
        with patch("urllib.request.urlopen", return_value=Response(payload)):
            self.assertEqual(private_chat_id("secret"), "123")

    def test_rejects_ambiguous_chats(self):
        payload = {"result": [
            {"message": {"text": "/start", "chat": {"id": 1, "type": "private"}}},
            {"message": {"text": "/start", "chat": {"id": 2, "type": "private"}}},
        ]}
        with patch("urllib.request.urlopen", return_value=Response(payload)):
            with self.assertRaises(RuntimeError):
                private_chat_id("secret")

    def test_ignores_groups_and_other_messages(self):
        payload = {"result": [
            {"message": {"text": "/start", "chat": {"id": 1, "type": "group"}}},
            {"message": {"text": "hello", "chat": {"id": 2, "type": "private"}}},
        ]}
        with patch("urllib.request.urlopen", return_value=Response(payload)):
            with self.assertRaises(RuntimeError):
                private_chat_id("secret")
