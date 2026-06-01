from __future__ import annotations

import json
import logging
import unittest

from kindly_web_search_mcp_server.models import WebSearchResult
from kindly_web_search_mcp_server.search.flow_observability import (
    serialize_query_variants,
    summarize_result_list,
)
from kindly_web_search_mcp_server.search.merge_observability import emit_merge_summary
from kindly_web_search_mcp_server.utils.observability import (
    emit_observability_event,
    serialize_search_results,
)


class _Variant:
    kind = "official_docs"
    target = "keyword"
    query = "fastmcp middleware docs"
    weight = 1.2
    why = "Prefer official docs"


class TestObservabilityFlow(unittest.TestCase):
    def test_search_result_serialization_includes_bounded_hashes(self) -> None:
        result = WebSearchResult(
            title="FastMCP docs",
            link="https://gofastmcp.com/servers/middleware",
            snippet="Middleware documentation",
            domain="gofastmcp.com",
            providers=["searxng"],
            provider_count=1,
            score=0.42,
        )

        serialized = serialize_search_results([result], max_results=1)[0]

        self.assertEqual(serialized["title_len"], len("FastMCP docs"))
        self.assertEqual(serialized["snippet_len"], len("Middleware documentation"))
        self.assertEqual(len(serialized["link_hash"]), 16)
        self.assertEqual(len(serialized["result_hash"]), 16)

    def test_search_result_serialization_handles_public_dict_results(self) -> None:
        serialized = serialize_search_results(
            [
                {
                    "title": "FastMCP docs",
                    "link": "https://gofastmcp.com/servers/middleware",
                    "snippet": "Middleware documentation",
                    "domain": "gofastmcp.com",
                    "providers": ["searxng"],
                    "provider_count": 1,
                }
            ],
            max_results=1,
        )[0]

        self.assertEqual(serialized["title"], "FastMCP docs")
        self.assertEqual(serialized["domain"], "gofastmcp.com")
        self.assertEqual(serialized["providers"], ["searxng"])
        self.assertEqual(len(serialized["result_hash"]), 16)

    def test_query_variant_and_result_list_summaries_are_hard_value_payloads(self) -> None:
        result = WebSearchResult(
            title="FastMCP docs",
            link="https://gofastmcp.com/servers/middleware",
            snippet="Middleware documentation",
            domain="gofastmcp.com",
            providers=["searxng", "ddg"],
        )

        variants = serialize_query_variants([_Variant()])
        summary = summarize_result_list(
            index=0,
            query="fastmcp middleware docs",
            providers=["searxng"],
            weight=1.2,
            results=[result],
        )

        self.assertEqual(variants[0]["weight"], 1.2)
        self.assertEqual(summary["provider_counts"], {"searxng": 1, "ddg": 1})
        self.assertEqual(summary["domain_counts"], {"gofastmcp.com": 1})
        self.assertEqual(summary["results"][0].providers, ["searxng", "ddg"])
        self.assertEqual(summary["top_results"][0]["domain"], "gofastmcp.com")

    def test_emit_observability_event_exposes_trace_fields_as_extra_keys(self) -> None:
        logger = logging.getLogger("test.observability.flow")
        with self.assertLogs(logger, level="INFO") as captured:
            emit_observability_event(logger, "probe.event", query="hello")

        payload = json.loads(captured.records[0].getMessage())
        self.assertEqual(payload["event"], "probe.event")
        self.assertEqual(payload["query"], "hello")
        self.assertTrue(hasattr(captured.records[0], "kindly_query"))

    def test_merge_summary_logs_counts_and_top_results(self) -> None:
        logger = logging.getLogger("test.observability.merge")
        result = WebSearchResult(
            title="FastMCP docs",
            link="https://gofastmcp.com/servers/middleware",
            snippet="Middleware documentation",
            domain="gofastmcp.com",
            providers=["searxng"],
        )

        with self.assertLogs(logger, level="INFO") as captured:
            emit_merge_summary(
                logger,
                result_lists=[[result]],
                output=[result],
                provider_contributions={"searxng": 1},
                list_weights=[1.0],
                k=60,
                discarded_count=0,
                overlap_rate=0.0,
                duration_seconds=0.001,
                max_per_host=2,
                host_cap_top_k=None,
            )

        payload = json.loads(captured.records[0].getMessage())
        self.assertEqual(payload["event"], "search.merge.summary")
        self.assertEqual(payload["input_result_count"], 1)
        self.assertEqual(payload["provider_contributions"], {"searxng": 1})
        self.assertEqual(payload["output_results"][0]["domain"], "gofastmcp.com")
        self.assertEqual(len(payload["top_results"][0]["link_hash"]), 16)


if __name__ == "__main__":
    unittest.main()
