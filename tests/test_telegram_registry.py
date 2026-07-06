"""Tests for Telegram channel registry."""

import tempfile
import unittest
from pathlib import Path

from kindly_web_search_mcp_server.search.telegram_registry import (
    TelegramRegistryDuckDB,
    TelegramChannelEntry,
)


class TestTelegramRegistry(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = str(Path(self.tmpdir) / "test_registry.duckdb")
        self.registry = TelegramRegistryDuckDB(db_path=self.db_path)

    def test_upsert_and_query(self):
        entries = [
            TelegramChannelEntry(
                username="python",
                title="Python",
                intent="ai_coding_and_infrastructure",
                member_count=50000,
            ),
            TelegramChannelEntry(
                username="devops",
                title="DevOps",
                intent="ai_coding_and_infrastructure",
                member_count=30000,
            ),
            TelegramChannelEntry(
                username="news_tech",
                title="Tech News",
                intent="news",
                member_count=100000,
            ),
        ]
        count = self.registry.upsert_channels(entries)
        self.assertEqual(count, 3)

        # Query by intent
        results = self.registry.get_channels_for_intent("ai_coding_and_infrastructure")
        self.assertEqual(len(results), 2)
        # Ordered by member_count desc
        self.assertEqual(results[0]["username"], "python")
        self.assertEqual(results[1]["username"], "devops")

    def test_upsert_updates_existing(self):
        self.registry.upsert_channels(
            [
                TelegramChannelEntry(username="python", title="Old Title", intent="general"),
            ]
        )
        self.registry.upsert_channels(
            [
                TelegramChannelEntry(username="python", title="New Title", intent="news"),
            ]
        )
        results = self.registry.get_channels_for_intent("news")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "New Title")

    def test_mark_joined(self):
        self.registry.upsert_channels(
            [
                TelegramChannelEntry(username="python", title="Python", intent="general"),
            ]
        )
        self.registry.mark_joined("python")
        unjoined = self.registry.get_unjoined_for_intent("general")
        self.assertEqual(len(unjoined), 0)

    def test_get_unjoined(self):
        self.registry.upsert_channels(
            [
                TelegramChannelEntry(username="python", title="Python", intent="general"),
                TelegramChannelEntry(username="golang", title="Go", intent="general"),
            ]
        )
        self.registry.mark_joined("python")
        unjoined = self.registry.get_unjoined_for_intent("general")
        self.assertEqual(unjoined, ["golang"])

    def test_total_channels(self):
        self.assertEqual(self.registry.total_channels(), 0)
        self.registry.upsert_channels(
            [
                TelegramChannelEntry(username="a", title="A"),
                TelegramChannelEntry(username="b", title="B"),
            ]
        )
        self.assertEqual(self.registry.total_channels(), 2)

    def test_empty_db_returns_empty(self):
        results = self.registry.get_channels_for_intent("nonexistent")
        self.assertEqual(results, [])
