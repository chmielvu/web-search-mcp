"""Tests for Telegram search provider (mocked Telethon)."""

import unittest
from unittest.mock import MagicMock

from kindly_web_search_mcp_server.search.providers.telegram import (
    _message_link,
    _chat_title,
)


class TestMessageLink(unittest.TestCase):
    def test_public_channel(self):
        msg = MagicMock()
        msg.chat.username = "python"
        msg.id = 12345
        self.assertEqual(_message_link(msg), "https://t.me/python/12345")

    def test_private_channel(self):
        msg = MagicMock()
        msg.chat.username = None
        msg.chat.id = -1001234567890
        msg.id = 12345
        link = _message_link(msg)
        self.assertIn("/c/", link)
        self.assertIn("12345", link)

    def test_no_chat(self):
        msg = MagicMock()
        msg.chat = None
        msg.id = 12345
        self.assertEqual(_message_link(msg), "")


class TestChatTitle(unittest.TestCase):
    def test_with_title(self):
        msg = MagicMock()
        msg.chat.title = "Python Chat"
        self.assertEqual(_chat_title(msg), "Python Chat")

    def test_no_chat(self):
        msg = MagicMock()
        msg.chat = None
        self.assertEqual(_chat_title(msg), "Telegram")


if __name__ == "__main__":
    unittest.main()
