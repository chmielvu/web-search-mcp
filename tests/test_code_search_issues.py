"""Tests for the code_search 'issues' mode (GitHub Issues/Discussions channel)."""

from __future__ import annotations

import json
import os
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import httpx

from kindly_web_search_mcp_server.tools.code_search.issues import (
    compile_issues_query,
    search_github_issues,
)
from kindly_web_search_mcp_server.tools.code_search.models import CodeSearchRequest
from kindly_web_search_mcp_server.tools.code_search.orchestrator import execute_code_search
from kindly_web_search_mcp_server.tools.code_search.query import build_query_plan
from kindly_web_search_mcp_server.tools.code_search.tool import _normalize_mode


def _request(**overrides) -> CodeSearchRequest:
    defaults: dict = {
        "query": "retry logic bug",
        "research_goal": "",
        "repositories": (),
        "language": None,
        "path": None,
        "filename": None,
        "extension": None,
        "regexp": False,
        "deep": False,
        "repo_name": None,
        "library_name": None,
        "topic": None,
        "mode": "issues",
    }
    defaults.update(overrides)
    return CodeSearchRequest(**defaults)


class TestNormalizeMode(IsolatedAsyncioTestCase):
    def test_issues_mode_is_accepted_case_insensitively(self) -> None:
        self.assertEqual(_normalize_mode("Issues"), "issues")
        self.assertEqual(_normalize_mode("ISSUES"), "issues")

    def test_unknown_mode_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _normalize_mode("conversations")


class TestCompileIssuesQuery(IsolatedAsyncioTestCase):
    def test_strips_code_only_qualifiers_and_regex_tokens(self) -> None:
        plan = build_query_plan(
            "retry logic /backoff\\d+/ path:src/ lang:python",
            regexp=False,
            repositories=(),
            language=None,
            path=None,
            filename=None,
            extension=None,
            mode="issues",
        )
        compiled = compile_issues_query(plan, _request(query=plan.original_query))
        self.assertNotIn("path:", compiled)
        self.assertNotIn("lang:", compiled)
        self.assertNotIn("/backoff", compiled)
        self.assertIn("retry", compiled)

    def test_injects_repo_scopes_from_request(self) -> None:
        plan = build_query_plan(
            "timeout handling",
            repositories=(),
            mode="issues",
        )
        compiled = compile_issues_query(
            plan, _request(repositories=("prefecthq/fastmcp",))
        )
        self.assertIn("repo:prefecthq/fastmcp", compiled)

    def test_preserves_existing_repo_qualifier(self) -> None:
        plan = build_query_plan(
            "repo:owner/repo timeout",
            repositories=(),
            mode="issues",
        )
        compiled = compile_issues_query(
            plan, _request(repositories=("other/repo",))
        )
        self.assertIn("repo:owner/repo", compiled)
        self.assertNotIn("other/repo", compiled)


def _issue_payload() -> dict:
    return {
        "data": {
            "search": {
                "edges": [
                    {
                        "node": {
                            "number": 12,
                            "title": "Retry storms under 429",
                            "url": "https://github.com/acme/demo/issues/12",
                            "createdAt": "2026-01-01T00:00:00Z",
                            "updatedAt": "2026-02-01T00:00:00Z",
                            "state": "OPEN",
                            "author": {"login": "jan"},
                            "comments": {"totalCount": 3},
                            "repository": {"nameWithOwner": "acme/demo"},
                        }
                    }
                ]
            }
        }
    }


def _discussion_payload() -> dict:
    return {
        "data": {
            "search": {
                "edges": [
                    {
                        "node": {
                            "number": 5,
                            "title": "Best backoff strategy?",
                            "url": "https://github.com/acme/demo/discussions/5",
                            "upvoteCount": 7,
                            "createdAt": "2026-01-02T00:00:00Z",
                            "updatedAt": "2026-02-02T00:00:00Z",
                            "author": {"login": "alex"},
                            "repository": {"nameWithOwner": "acme/demo"},
                            "comments": {"totalCount": 9},
                        }
                    }
                ]
            }
        }
    }


def _graphql_transport(seen: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        query_text = body["query"]
        seen.append(body)
        if "type: ISSUE" in query_text:
            return httpx.Response(200, json=_issue_payload())
        if "type: DISCUSSION" in query_text:
            return httpx.Response(200, json=_discussion_payload())
        raise AssertionError(f"unexpected GraphQL document: {query_text[:80]}")

    return httpx.MockTransport(handler)


class TestSearchGithubIssues(IsolatedAsyncioTestCase):
    async def test_requires_token(self) -> None:
        plan = build_query_plan("retry logic", mode="issues")
        with patch.dict(os.environ, {"GITHUB_TOKEN": "", "GH_TOKEN": ""}):
            response = await search_github_issues(
                plan, _request(), http_client=httpx.AsyncClient()
            )
        self.assertEqual(response.hits, [])
        self.assertEqual(response.diagnostics[0].failure_kind, "auth")

    async def test_merges_issue_and_discussion_hits(self) -> None:
        seen: list[dict] = []
        plan = build_query_plan("retry logic", mode="issues")
        with patch.dict(os.environ, {"GITHUB_TOKEN": "t"}):
            async with httpx.AsyncClient(transport=_graphql_transport(seen)) as client:
                response = await search_github_issues(
                    plan, _request(max_results=10), http_client=client
                )
        self.assertEqual(len(seen), 2)
        kinds = {hit.evidence_role for hit in response.hits}
        self.assertEqual(kinds, {"issue", "discussion"})
        self.assertEqual(response.hits[0].provider, "github")
        self.assertEqual(response.hits[0].location.precision, "url")
        self.assertEqual(response.metadata["compiled_queries"][0].startswith("type:ISSUE"), True)
        self.assertEqual(response.request_count, 2)

    async def test_graphql_errors_become_diagnostics(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"errors": [{"message": "bad query"}]})

        plan = build_query_plan("retry logic", mode="issues")
        with patch.dict(os.environ, {"GITHUB_TOKEN": "t"}):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                response = await search_github_issues(
                    plan, _request(), http_client=client
                )
        self.assertEqual(response.hits, [])
        self.assertTrue(response.diagnostics)
        self.assertIn("bad query", response.diagnostics[0].message)


class TestOrchestratorIssuesMode(IsolatedAsyncioTestCase):
    async def test_issues_mode_runs_exclusively(self) -> None:
        from kindly_web_search_mcp_server.tools.code_search.models import ProviderResponse

        async with httpx.AsyncClient() as client:
            with (
                patch(
                    "kindly_web_search_mcp_server.tools.code_search.orchestrator.search_github_issues",
                    new_callable=AsyncMock,
                    return_value=ProviderResponse(provider="github"),
                ) as issues,
                patch(
                    "kindly_web_search_mcp_server.tools.code_search.orchestrator.search_github",
                    new_callable=AsyncMock,
                    side_effect=AssertionError("github must not run in issues mode"),
                ),
                patch(
                    "kindly_web_search_mcp_server.tools.code_search.orchestrator.search_sourcegraph",
                    new_callable=AsyncMock,
                    side_effect=AssertionError("sourcegraph must not run in issues mode"),
                ),
                patch(
                    "kindly_web_search_mcp_server.tools.code_search.orchestrator.search_grepapp",
                    new_callable=AsyncMock,
                    side_effect=AssertionError("grep.app must not run in issues mode"),
                ),
                patch(
                    "kindly_web_search_mcp_server.tools.code_search.orchestrator.search_exa",
                    new_callable=AsyncMock,
                    side_effect=AssertionError("exa must not run in issues mode"),
                ),
            ):
                result = await execute_code_search(
                    _request(max_results=5),
                    build_query_plan("retry logic", mode="issues"),
                    http_client=client,
                )
        issues.assert_awaited_once()
        self.assertEqual(result.stats.provider_counts, {"github": 0})
