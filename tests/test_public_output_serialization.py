from __future__ import annotations

import unittest

from kindly_web_search_mcp_server.models import WebSearchHit, WebSearchResult
from kindly_web_search_mcp_server.utils.public_output import (
    decode_web_search_overflow_cursor,
    encode_web_search_overflow_cursor,
    page_overflow_cursor,
    to_public_hit,
    to_public_web_search,
)


def _hit(**overrides: object) -> WebSearchResult:
    payload = {
        "title": "FastMCP docs",
        "link": "https://gofastmcp.com/docs",
        "snippet": "Tool registration",
        "domain": "gofastmcp.com",
        "published_date": "2026-05-01",
        "providers": ["searxng"],
        "score": 0.9,
    }
    payload.update(overrides)
    return WebSearchResult(**payload)  # type: ignore[arg-type]


class TestPublicOutputSerialization(unittest.TestCase):
    def test_to_public_hit_maps_url_and_omits_internal_fields(self) -> None:
        public = to_public_hit(_hit(), "c1")
        dumped = public.model_dump()
        self.assertEqual(dumped["citation_id"], "c1")
        self.assertEqual(dumped["url"], "https://gofastmcp.com/docs")
        self.assertNotIn("link", dumped)
        self.assertNotIn("fetch_hint", dumped)
        self.assertNotIn("evidence_score", dumped)
        self.assertEqual(dumped["consensus"], 1)
        self.assertEqual(dumped["providers"], ["searxng"])

    def test_to_public_web_search_page1_and_cursor_round_trip(self) -> None:
        hits = [_hit(link=f"https://example.com/{index}") for index in range(2)]
        overflow = [
            ("rankllm", _hit(title="Later", link="https://example.com/overflow")),
        ]
        public = to_public_web_search(
            query="fastmcp docs",
            intent="factual",
            query_variants={"original": "fastmcp docs"},
            hits=hits,
            overflow_items=overflow,
            warnings=None,
        )
        dumped = public.model_dump()
        self.assertEqual(dumped["status"], "ok")
        self.assertEqual(len(dumped["results"]), 2)
        self.assertIsInstance(public.results[0], WebSearchHit)
        self.assertTrue(dumped["has_more"])
        self.assertEqual(dumped["remaining"], 1)
        self.assertEqual(dumped["next"][0]["tool"], "fetch")
        self.assertNotIn("cursor", dumped["next"][0]["query"])
        decoded = decode_web_search_overflow_cursor(public.cursor or "")
        self.assertEqual(decoded["items"][0]["stage"], "rankllm")
        self.assertEqual(decoded["citation_base"], 2)

    def test_cursor_round_trip_pages_overflow_hits(self) -> None:
        encoded = encode_web_search_overflow_cursor(
            {
                "v": 1,
                "kind": "web_search_overflow",
                "query": "q",
                "intent": None,
                "query_variants": None,
                "citation_base": 15,
                "items": [{"title": "T", "url": "https://example.com/16", "stage": "cross"}],
            }
        )
        paged = page_overflow_cursor(decode_web_search_overflow_cursor(encoded))
        dumped = paged.model_dump()
        self.assertEqual(dumped["results"][0]["citation_id"], "c16")
        self.assertEqual(dumped["results"][0]["stage"], "cross")
        self.assertNotIn("snippet", dumped["results"][0])
        self.assertNotIn("cursor", dumped)

    def test_hit_without_score_or_providers_omits_optional_fields(self) -> None:
        public = to_public_hit(
            WebSearchResult(title="T", link="https://example.com", snippet="S"),
            "c1",
        )
        dumped = public.model_dump()
        for forbidden in (
            "score",
            "consensus",
            "providers",
            "fetch_hint",
            "link",
            "query_shaping",
        ):
            self.assertNotIn(forbidden, dumped)


if __name__ == "__main__":
    unittest.main()
