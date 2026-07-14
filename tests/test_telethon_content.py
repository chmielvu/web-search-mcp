"""Tests for Telegram content resolver (URL parsing only — no API calls)."""

import unittest
from kindly_web_search_mcp_server.content.resolvers.telegram import (
    parse_telegram_url,
    TelegramContentError,
)


class TestParseTelegramUrl(unittest.TestCase):
    def test_channel_message(self):
        target = parse_telegram_url("https://t.me/python/12345")
        self.assertEqual(target.username, "python")
        self.assertIsNone(target.channel_id)
        self.assertEqual(target.msg_id, 12345)
        self.assertIsNone(target.comment_thread_id)

    def test_channel_id_message(self):
        target = parse_telegram_url("https://t.me/c/1234567890/12345")
        self.assertIsNone(target.username)
        self.assertEqual(target.channel_id, 1234567890)
        self.assertEqual(target.msg_id, 12345)

    def test_channel_only(self):
        target = parse_telegram_url("https://t.me/python")
        self.assertEqual(target.username, "python")
        self.assertIsNone(target.msg_id)

    def test_with_thread(self):
        target = parse_telegram_url("https://t.me/python/12345?thread=67890")
        self.assertEqual(target.username, "python")
        self.assertEqual(target.msg_id, 12345)
        self.assertEqual(target.comment_thread_id, 67890)

    def test_telegram_me_host(self):
        target = parse_telegram_url("https://telegram.me/python/12345")
        self.assertEqual(target.username, "python")

    def test_non_telegram_url_raises(self):
        with self.assertRaises(TelegramContentError):
            parse_telegram_url("https://example.com/foo")

    def test_invalid_path_raises(self):
        with self.assertRaises(TelegramContentError):
            parse_telegram_url("https://t.me/")

    def test_trailing_slash(self):
        target = parse_telegram_url("https://t.me/python/")
        self.assertEqual(target.username, "python")
        self.assertIsNone(target.msg_id)

    def test_channel_id_no_message(self):
        target = parse_telegram_url("https://t.me/c/1234567890")
        self.assertEqual(target.channel_id, 1234567890)
        self.assertIsNone(target.msg_id)


if __name__ == "__main__":
    unittest.main()
