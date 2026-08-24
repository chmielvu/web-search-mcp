"""Mocked tests for Apify-backed resolvers: twitter.py + reddit Layer 4."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from kindly_web_search_mcp_server.content.resolvers import reddit as reddit_resolver
from kindly_web_search_mcp_server.content.resolvers import twitter as twitter_resolver
from kindly_web_search_mcp_server.settings import settings


class _FakeApifyClient:
    def __init__(self, items: list[dict[str, object]]) -> None:
        self.items = items
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def run_sync_get_dataset_items(
        self, actor: str, run_input: dict[str, object]
    ) -> list[dict[str, object]]:
        self.calls.append((actor, run_input))
        return self.items


class TwitterParseTests(unittest.TestCase):
    def test_parse_status_url_forms(self) -> None:
        for url in (
            "https://x.com/jack/status/123",
            "https://twitter.com/jack/status/123/",
            "https://x.com/i/web/status/123",
            "https://mobile.twitter.com/status/123",
        ):
            with self.subTest(url=url):
                target = twitter_resolver.parse_twitter_url(url)
                self.assertEqual(target.tweet_id, "123")
                self.assertIsNone(target.screen_name)

    def test_parse_profile_url(self) -> None:
        target = twitter_resolver.parse_twitter_url("https://x.com/jack")
        self.assertEqual(target.screen_name, "jack")
        self.assertEqual(target.tweet_id, "")

    def test_parse_rejects_foreign_and_reserved_paths(self) -> None:
        for bad in (
            "https://example.com/jack/status/1",
            "https://x.com/search?q=hi",
            "https://x.com/i/lists/123",
        ):
            with self.subTest(url=bad), self.assertRaises(twitter_resolver.TwitterError):
                twitter_resolver.parse_twitter_url(bad)


class TwitterFetchTests(unittest.IsolatedAsyncioTestCase):
    URL = "https://x.com/jack/status/123"

    @patch.object(twitter_resolver, "get_apify_client")
    async def test_fetch_tweet_success(self, mock_get_client) -> None:
        fake = _FakeApifyClient(
            [
                {
                    "id": "123",
                    "userName": "jack",
                    "name": "Jack D",
                    "text": "hello world",
                    "favoriteCount": 5,
                }
            ]
        )
        mock_get_client.return_value = fake

        markdown = await twitter_resolver.fetch_twitter_markdown(self.URL)

        self.assertIn("@jack (Jack D)", markdown)
        self.assertIn("hello world", markdown)
        self.assertIn("Likes: 5", markdown)
        actor, run_input = fake.calls[0]
        self.assertIn("~", actor)
        urls = run_input.get("urls")
        if not isinstance(urls, list) or not urls:
            self.fail("Apify run input did not contain a URL list")
        self.assertTrue(str(urls[0]).endswith("/status/123"))

    @patch.object(twitter_resolver, "get_apify_client", return_value=None)
    async def test_fetch_without_token_raises(self, _mock) -> None:
        with self.assertRaises(twitter_resolver.TwitterError):
            await twitter_resolver.fetch_twitter_markdown(self.URL)

    @patch.object(twitter_resolver, "get_apify_client")
    async def test_fetch_no_renderable_items_raises(self, mock_get_client) -> None:
        mock_get_client.return_value = _FakeApifyClient([{"id": "999", "text": ""}])
        with self.assertRaises(twitter_resolver.TwitterError):
            await twitter_resolver.fetch_twitter_markdown(self.URL)


class RedditApifyLayerOrderTests(unittest.IsolatedAsyncioTestCase):
    URL = "https://www.reddit.com/r/python/comments/abc123/some_title/"

    def setUp(self) -> None:
        self.calls: list[str] = []

        async def direct(_target: object) -> str:
            self.calls.append("direct")
            raise RuntimeError("direct down")

        async def old_html(_target: object) -> str:
            self.calls.append("old")
            raise RuntimeError("old down")

        async def arctic(_target: object) -> str:
            self.calls.append("arctic")
            raise RuntimeError("arctic down")

        async def apify(_target: object, *, url: str) -> str:
            self.calls.append("apify")
            return "# apify ok"

        self._patches = [
            patch.object(reddit_resolver, "_fetch_reddit_direct_json", direct),
            patch.object(reddit_resolver, "_fetch_old_reddit_html", old_html),
            patch.object(reddit_resolver, "_fetch_reddit_arctic_shift", arctic),
            patch.object(reddit_resolver, "_fetch_reddit_via_apify", apify),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()

    async def test_free_layers_first_by_default(self) -> None:
        with patch.object(settings, "apify_reddit_first", False):
            md = await reddit_resolver.fetch_reddit_thread_markdown(self.URL)
        self.assertEqual(self.calls, ["direct", "old", "arctic", "apify"])
        self.assertEqual(md, "# apify ok")

    async def test_apify_layer_first_with_flag(self) -> None:
        with patch.object(settings, "apify_reddit_first", True):
            md = await reddit_resolver.fetch_reddit_thread_markdown(self.URL)
        self.assertEqual(self.calls, ["apify"])
        self.assertEqual(md, "# apify ok")

    async def test_all_layers_fail_raises(self) -> None:
        async def apify_down(_target: object, *, url: str) -> str:
            raise RuntimeError("apify down")

        with (
            patch.object(reddit_resolver, "_fetch_reddit_via_apify", apify_down),
            patch.object(settings, "apify_reddit_first", False),
        ):
            with self.assertRaises(reddit_resolver.RedditError):
                await reddit_resolver.fetch_reddit_thread_markdown(self.URL)


class ApifyRedditMappingTests(unittest.TestCase):
    TARGET = reddit_resolver.RedditTarget(subreddit="python", post_id="abc123")

    def test_post_item_maps_to_renderer_shape(self) -> None:
        item = {
            "id": "abc123",
            "title": "Hello world",
            "selftext": "Body text here",
            "author": "alice",
            "score": 10,
            "subreddit": "python",
            "permalink": "/r/python/comments/abc123/",
        }
        post = reddit_resolver._apify_item_to_post_data(item, self.TARGET)
        self.assertIsNotNone(post)
        self.assertEqual(post["title"], "Hello world")  # type: ignore[index]
        self.assertEqual(post["author"], "alice")  # type: ignore[index]

    def test_non_post_item_returns_none(self) -> None:
        self.assertIsNone(
            reddit_resolver._apify_item_to_post_data({"body": "just a comment"}, self.TARGET)
        )

    def test_comments_flatten_into_t1_children(self) -> None:
        children = reddit_resolver._apify_items_to_comment_children(
            [{"body": "nice post", "author": "bob", "score": 3}, "not-a-dict"]  # type: ignore[list-item]
        )
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0]["kind"], "t1")
        self.assertEqual(children[0]["data"]["body"], "nice post")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
