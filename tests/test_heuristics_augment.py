"""Behavior contracts for query features and provider augmentation."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from kindly_web_search_mcp_server.heuristics.augment import (
    augment_query_for_provider,
    specialized_fallback_query,
)
from kindly_web_search_mcp_server.heuristics.query_features import build_query_features
from kindly_web_search_mcp_server.models import (
    ProviderWarning,
    WebSearchResponse,
    WebSearchResult,
)
from kindly_web_search_mcp_server.utils.public_output import serialize_public_web_search_response


class TestHeuristicsAugment(unittest.TestCase):
    def test_augment_github_adds_language(self) -> None:
        features = build_query_features("async context manager python")
        aug = augment_query_for_provider("github", features.cleaned, features)
        self.assertIn("language:Python", aug.query)
        self.assertIn("github.language", aug.rules_applied)

    def test_augment_github_preserves_repo(self) -> None:
        q = "repo:a/b foo"
        features = build_query_features(q)
        aug = augment_query_for_provider("github", q, features)
        self.assertIn("repo:a/b", aug.query)
        # Should not invent a second repo:
        self.assertEqual(aug.query.count("repo:"), 1)

    def test_augment_github_removes_unsupported_and_wildcard_qualifiers(self) -> None:
        query = "patternType:regexp repo:* lang:python file:src/main.py needle"
        features = build_query_features(query)
        aug = augment_query_for_provider("github", query, features)
        self.assertNotIn("patternType:", aug.query)
        self.assertNotIn("repo:*", aug.query)
        self.assertIn("language:python", aug.query.casefold())
        self.assertIn("path:src/main.py", aug.query.casefold())

    def test_augment_sourcegraph_records_regexp_metadata_without_marker(self) -> None:
        query = "patternType:regexp needle python"
        features = build_query_features(query)
        aug = augment_query_for_provider("sourcegraph", query, features)
        self.assertNotIn("patternType:", aug.query)
        self.assertEqual(dict(aug.metadata)["pattern_type"], "regexp")

    def test_augment_gitlab_strips_unsupported_code_qualifiers(self) -> None:
        query = "repo:acme/demo language:python path:src/main.py needle"
        features = build_query_features(query)
        aug = augment_query_for_provider("gitlab", query, features)
        self.assertNotIn("repo:", aug.query)
        self.assertNotIn("language:", aug.query)
        self.assertNotIn("path:", aug.query)
        self.assertEqual(dict(aug.metadata)["project_hint"], "acme/demo")

    def test_augment_sourcegraph_adds_lang(self) -> None:
        features = build_query_features("asyncio gather timeout python")
        aug = augment_query_for_provider("sourcegraph", features.cleaned, features)
        self.assertIn("lang:python", aug.query.casefold())
        self.assertIn("sg.lang", aug.rules_applied)

    def test_augment_hn_strips_repo(self) -> None:
        features = build_query_features("repo:x/y performance")
        aug = augment_query_for_provider("hackernews", "repo:x/y performance", features)
        self.assertNotIn("repo:", aug.query)
        self.assertIn("hn.strip_ops", aug.rules_applied)

    def test_build_features_repo_and_preserved(self) -> None:
        understanding = SimpleNamespace(
            intent="ai_coding_and_infrastructure",
            preserved_terms=["OpenTelemetry"],
            domain_hints=["python"],
        )
        features = build_query_features(
            "owner/name crash loop",
            understanding=understanding,
            support_terms=["span"],
        )
        self.assertEqual(features.intent, "ai_coding_and_infrastructure")
        self.assertIn("OpenTelemetry", features.preserved_terms)
        self.assertIn("span", features.preserved_terms)
        self.assertIn("Python", features.languages)
        self.assertTrue(any("owner/name" in s for s in features.repo_slugs))

    def test_specialized_fallback_coding_uses_sourcegraph(self) -> None:
        features = build_query_features("python asyncio timeout")
        shaped = specialized_fallback_query("ai_coding_and_infrastructure", features)
        self.assertIn("lang:", shaped.casefold())

    def test_public_output_includes_shaping(self) -> None:
        result = WebSearchResult(
            title="t",
            link="https://example.com",
            snippet="s",
            providers=["github"],
            provider_count=1,
        )
        response = WebSearchResponse(
            query="python asyncio",
            results=[result],
            total_results=1,
            providers_used=["github"],
            warnings=[ProviderWarning(provider="ddg", error="x", error_type="timeout")],
            intent="ai_coding_and_infrastructure",
            query_shaping=[
                {
                    "provider": "github",
                    "branch_role": "specialized",
                    "original": "python asyncio",
                    "shaped": "python asyncio language:Python",
                    "rules": ["github.language"],
                }
            ],
        )
        public = serialize_public_web_search_response(response)
        self.assertEqual(public["intent"], "ai_coding_and_infrastructure")
        self.assertIsInstance(public["query_shaping"], list)
        self.assertEqual(public["query_shaping"][0]["provider"], "github")
        self.assertNotIn("diagnostics", public)


if __name__ == "__main__":
    unittest.main()
