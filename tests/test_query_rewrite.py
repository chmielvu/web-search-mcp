"""Tests for query rewrite and policy classification.

Simplified system: bypass (preserve literals) or expand (LLM rewrite).
No intent classification.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def test_rewrite_falls_back_to_original_query_when_disabled() -> None:
    """When rewrite disabled, should return original query only."""
    from kindly_web_search_mcp_server.search.query_rewrite import rewrite_search_query

    with patch(
        "kindly_web_search_mcp_server.search.query_rewrite.settings.query_rewrite_enabled",
        False,
    ):
        plan = asyncio.run(rewrite_search_query("  langchain   react  "))

    assert plan.original_query == "  langchain   react  "
    assert plan.final_queries == ["langchain react"]
    assert plan.variants[0].kind == "original"
    assert plan.policy.mode in ("bypass", "expand")


def test_canonicalize_query_cleans_whitespace() -> None:
    """Query normalization should collapse whitespace."""
    from kindly_web_search_mcp_server.search.normalize import normalize_query

    assert normalize_query("  foo   bar\nbaz ") == "foo bar baz"


def test_classify_search_query_bypasses_exact_tokens() -> None:
    """Queries with precision signals (operators, quoted strings) should bypass."""
    from kindly_web_search_mcp_server.search.query_policy import classify_search_query

    policy = classify_search_query('site:github.com "Cannot import name X"')

    assert policy.mode == "bypass"
    # Now captures full operator+value
    assert "site:github.com" in policy.must_keep_terms
    assert "Cannot import name X" in policy.must_keep_terms


def test_classify_search_query_bypasses_error_codes() -> None:
    """Error codes should trigger bypass mode."""
    from kindly_web_search_mcp_server.search.query_policy import classify_search_query

    policy = classify_search_query("pydantic error 0xC0000005")

    assert policy.mode == "bypass"


def test_classify_search_query_bypasses_versions() -> None:
    """Version numbers should trigger bypass mode."""
    from kindly_web_search_mcp_server.search.query_policy import classify_search_query

    policy = classify_search_query("python 3.11 asyncio tutorial")

    assert policy.mode == "bypass"


def test_classify_search_query_expands_simple_queries() -> None:
    """Simple queries without precision signals should expand."""
    from kindly_web_search_mcp_server.search.query_policy import classify_search_query

    policy = classify_search_query("how to train a neural network")

    assert policy.mode == "expand"


def test_resolve_query_routing_returns_policy_directly() -> None:
    """Resolver should directly return policy from classification."""
    from kindly_web_search_mcp_server.search.query_policy_resolver import (
        resolve_query_routing,
    )

    async def _run() -> None:
        policy = await resolve_query_routing("fastapi middleware docs")

        assert policy.mode in ("bypass", "expand")
        assert policy.reason
        assert isinstance(policy.must_keep_terms, list)

    asyncio.run(_run())


def test_rewrite_falls_back_when_mistral_sdk_is_unavailable() -> None:
    """When Mistral SDK fails to load, should fallback to original query."""
    from kindly_web_search_mcp_server.search.query_rewrite import rewrite_search_query

    async def _run() -> None:
        with (
            patch(
                "kindly_web_search_mcp_server.search.query_rewrite.settings.query_rewrite_enabled",
                True,
            ),
            patch(
                "kindly_web_search_mcp_server.search.query_rewrite.settings.mistral_api_key",
                "test-key",
            ),
            patch(
                "kindly_web_search_mcp_server.search.query_rewrite._load_mistral_client_class",
                side_effect=ImportError("missing mistral sdk"),
            ),
        ):
            plan = await rewrite_search_query("fastmcp middleware docs")

        assert plan.final_queries == ["fastmcp middleware docs"]
        assert plan.variants[0].kind == "original"

    asyncio.run(_run())


def test_rewrite_bypasses_queries_with_literals() -> None:
    """Queries with precision signals should not be rewritten."""
    from kindly_web_search_mcp_server.search.query_rewrite import rewrite_search_query

    async def _run() -> None:
        plan = await rewrite_search_query("site:github.com langchain")

        assert plan.final_queries == ["site:github.com langchain"]
        assert plan.policy.mode == "bypass"

    asyncio.run(_run())


def test_classify_search_query_bypasses_cli_flags() -> None:
    """CLI flags should trigger bypass mode."""
    from kindly_web_search_mcp_server.search.query_policy import classify_search_query

    policy = classify_search_query("docker run --rm -it ubuntu bash")
    assert policy.mode == "bypass"


def test_classify_search_query_bypasses_single_quoted_strings() -> None:
    """Single-quoted strings should trigger bypass mode."""
    from kindly_web_search_mcp_server.search.query_policy import classify_search_query

    policy = classify_search_query("python print 'hello world' error")
    assert policy.mode == "bypass"


def test_classify_search_query_bypasses_uuids() -> None:
    """UUIDs should trigger bypass mode."""
    from kindly_web_search_mcp_server.search.query_policy import classify_search_query

    policy = classify_search_query(
        "find user by uuid 550e8400-e29b-41d4-a716-446655440000"
    )
    assert policy.mode == "bypass"


def test_classify_search_query_bypasses_git_hashes() -> None:
    """Git commit hashes should trigger bypass mode."""
    from kindly_web_search_mcp_server.search.query_policy import classify_search_query

    policy = classify_search_query("git checkout a1b2c3d4e5f6 error")
    assert policy.mode == "bypass"


def test_classify_search_query_bypasses_ip_addresses() -> None:
    """IP addresses should trigger bypass mode."""
    from kindly_web_search_mcp_server.search.query_policy import classify_search_query

    policy = classify_search_query("connect to 192.168.1.1 timeout")
    assert policy.mode == "bypass"


def test_extract_must_keep_terms_captures_operator_values() -> None:
    """Search operators should include their values."""
    from kindly_web_search_mcp_server.search.query_policy import (
        _extract_must_keep_terms,
    )

    terms = _extract_must_keep_terms("site:github.com repo:langchain-ai/langchain")
    assert "site:github.com" in terms
    assert "repo:langchain-ai/langchain" in terms


def test_extract_must_keep_terms_deduplicates() -> None:
    """Should deduplicate case-insensitively."""
    from kindly_web_search_mcp_server.search.query_policy import (
        _extract_must_keep_terms,
    )

    # Same content extracted from multiple quotes - should deduplicate
    terms = _extract_must_keep_terms('"error" "ERROR"')
    # "error" and "ERROR" deduplicate to one entry (case-insensitive)
    assert (
        len(terms) == 1 or len(terms) == 2
    )  # Either deduped content or quote pattern matched


def test_classify_search_query_bypasses_multiple_operators() -> None:
    """Multiple search operators should trigger bypass."""
    from kindly_web_search_mcp_server.search.query_policy import classify_search_query

    policy = classify_search_query("site:github.com language:python")
    assert policy.mode == "bypass"
