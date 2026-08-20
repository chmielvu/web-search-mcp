from __future__ import annotations

import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch
from typing import cast

import httpx

from kindly_web_search_mcp_server.tools.code_search.docs import search_context7, search_deepwiki
from kindly_web_search_mcp_server.tools.code_search.exa import search_exa
from kindly_web_search_mcp_server.tools.code_search.github import (
    _RequestGate,
    _build_code_query,
    _build_repository_query,
    _build_repository_queries,
    _fragment_from_match,
    _is_low_value_global_discovery_hit,
    _parse_code_items,
    _probe_repo_state,
    _rank_repository_candidates,
    _repository_proof_variants,
    _search_scope_variant,
    hydrate_github_hits,
    search_github,
)
from kindly_web_search_mcp_server.tools.code_search.grepapp import (
    _exception_chain,
    parse_grepapp_text,
    _parse_rest_payload,
)
from kindly_web_search_mcp_server.tools.code_search.models import (
    CodeSearchHit,
    CodeSearchRequest,
    CodeSearchResultType,
    Diagnostic,
    ProviderResponse,
    QueryMetadata,
    RepoCandidate,
    Stats,
    TextFragment,
    to_public_file,
    to_public_result,
)
from kindly_web_search_mcp_server.tools.code_search.orchestrator import execute_code_search
from kindly_web_search_mcp_server.tools.code_search.query import build_query_plan
from kindly_web_search_mcp_server.tools.code_search.ranking import rank_hits
from kindly_web_search_mcp_server.tools.code_search.reranking import rerank_code_hits
from kindly_web_search_mcp_server.tools.code_search.sourcegraph import _parse_payload
from kindly_web_search_mcp_server.tools.code_search.tool import _validate_request
from kindly_web_search_mcp_server.rerank.models import RerankResult


class FakeResponse:
    def __init__(self, payload, status_code: int = 200, *, text: str | None = None, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.text = text if text is not None else str(payload)
        self.headers = headers or {}

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, *, get_responses=None, post_responses=None):
        self.get_responses = list(get_responses or [])
        self.post_responses = list(post_responses or [])
        self.get_calls: list[tuple[str, dict]] = []
        self.post_calls: list[tuple[str, dict]] = []

    async def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        if not self.get_responses:
            raise AssertionError(f"unexpected GET {url}")
        response = self.get_responses.pop(0)
        return response() if callable(response) else response

    async def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        if not self.post_responses:
            raise AssertionError(f"unexpected POST {url}")
        response = self.post_responses.pop(0)
        return response() if callable(response) else response


class TestQueryPlanner(IsolatedAsyncioTestCase):
    async def test_plural_implementations_activates_repository_discovery(self):
        plan = build_query_plan(
            "find Python GitHub code search implementations", mode="discovery"
        )
        self.assertEqual(plan.mode, "discovery")
        self.assertIn("repository", plan.metadata.backend_channels)

    async def test_regex_anchors_variants_and_separate_scopes(self):
        plan = build_query_plan(
            "how do people implement /retry-after/i backoff",
            deep=True,
            repositories=["owner/repo"],
            language="Python",
        )
        self.assertEqual(plan.regex_source, "retry-after")
        self.assertIsNotNone(plan.local_regex)
        self.assertIn("retry-after", plan.anchor_terms)
        self.assertLessEqual(len(plan.variants), 3)
        self.assertNotIn(("repo", "owner/repo"), plan.qualifiers)
        self.assertIn(("language", "Python"), plan.qualifiers)

    async def test_malformed_regexp_is_reported_without_provider_call(self):
        plan = build_query_plan("/[unclosed/", regexp=True)
        self.assertIsNone(plan.local_regex)
        self.assertTrue(any("Malformed" in warning for warning in plan.warnings))

    async def test_legacy_qualifiers_quotes_and_boolean_tokens_are_preserved(self):
        plan = build_query_plan(
            '("retry after" OR backoff) path:"src clients" language:Python fork:true'
        )
        self.assertEqual(
            plan.qualifiers,
            (("path", '"src clients"'), ("language", "Python"), ("fork", "true")),
        )
        self.assertIn("OR", plan.search_text)
        self.assertIn("(", plan.search_text)


class TestGithubAdapter(IsolatedAsyncioTestCase):
    async def test_code_query_compiles_supported_legacy_qualifiers(self):
        request = CodeSearchRequest(query="verify", language="Python", path="src")
        plan = build_query_plan("verify", language=request.language, path=request.path)
        query = _build_code_query("verify", scope="owner/repo", request=request, plan=plan)
        self.assertEqual(query, "verify repo:owner/repo language:Python path:src")

    async def test_repository_query_keeps_repo_filters_and_drops_code_filters(self):
        plan = build_query_plan("oauth stars:>100 topic:security filename:client.py fork:true")
        self.assertEqual(
            _build_repository_query(plan),
            "oauth stars:>100 topic:security fork:true in:name,description,readme",
        )

    async def test_repository_query_removes_discovery_prose_and_infers_language(self):
        plan = build_query_plan(
            "find open source Python GitHub GraphQL code search implementations"
        )
        self.assertEqual(
            _build_repository_query(plan),
            "GitHub GraphQL language:Python in:name,description,readme",
        )

    async def test_repository_ranking_prefers_exact_tool_over_high_star_list(self):
        plan = build_query_plan("find Python GitHub GraphQL code search implementations")
        ranked = _rank_repository_candidates(
            plan,
            [
                RepoCandidate(
                    name_with_owner="org/awesome-github-projects",
                    description="Curated list of GitHub resources",
                    stars=50_000,
                    discovery_queries=["broad"],
                ),
                RepoCandidate(
                    name_with_owner="org/github-code-search",
                    description="GraphQL-assisted GitHub code search tool",
                    stars=12,
                    discovery_queries=["precise", "broad"],
                ),
            ],
        )
        self.assertEqual(ranked[0].name_with_owner, "org/github-code-search")
        self.assertGreater(ranked[0].discovery_score, ranked[1].discovery_score)
        self.assertEqual(
            _build_repository_queries(plan),
            (
                '"code search" GitHub GraphQL language:Python in:name,description,readme',
                "GitHub GraphQL language:Python in:name,description,readme",
            ),
        )

    async def test_repository_proof_variants_use_source_shaped_identifiers(self):
        plan = build_query_plan("find Python GitHub code search implementations")
        self.assertEqual(
            _repository_proof_variants(plan, plan.variants),
            ("code_search", "search_code"),
        )

    async def test_global_discovery_noise_filter_keeps_source_files(self):
        self.assertTrue(
            _is_low_value_global_discovery_hit(
                CodeSearchHit(provider="github", path="docs/code-search.md")
            )
        )
        self.assertTrue(
            _is_low_value_global_discovery_hit(
                CodeSearchHit(provider="github", path="fixtures/results.jsonl")
            )
        )
        self.assertFalse(
            _is_low_value_global_discovery_hit(
                CodeSearchHit(provider="github", path="src/code_search.py")
            )
        )

    async def test_regex_uses_literal_anchor_for_legacy_api(self):
        request = CodeSearchRequest(query=r"/retry[_-]after/")
        plan = build_query_plan(request.query)
        query = _build_code_query(plan.variants[0], scope=None, request=request, plan=plan)
        self.assertNotIn("[_-]", query)
        self.assertIn("retry", query)

    async def test_text_match_fragment_keeps_offsets_relative_to_original_text(self):
        fragment = _fragment_from_match(
            {
                "fragment": "  retry_after()",
                "property": "content",
                "matches": [{"text": "retry", "indices": [2, 7]}],
            }
        )
        self.assertIsNotNone(fragment)
        self.assertEqual(fragment.text, "  retry_after()")
        self.assertEqual(fragment.match_metadata["matches"][0]["indices"], [2, 7])

    async def test_missing_token_is_typed_partial_auth_diagnostic(self):
        client = FakeClient()
        with patch.dict(os.environ, {"GITHUB_TOKEN": "", "GH_TOKEN": ""}):
            response = await search_github(
                build_query_plan("retry"),
                CodeSearchRequest(query="retry"),
                http_client=client,
            )
        self.assertEqual(response.hits, [])
        self.assertEqual(response.diagnostics[0].failure_kind, "auth")
        self.assertEqual(response.diagnostics[0].outcome, "partial")
        self.assertEqual(client.get_calls, [])

    async def test_text_match_parser_preserves_fragments_and_rank(self):
        hits, total, incomplete = _parse_code_items(
            {
                "total_count": 1,
                "incomplete_results": True,
                "items": [
                    {
                        "repository": {
                            "full_name": "owner/repo",
                            "html_url": "https://github.com/owner/repo",
                        },
                        "path": "src/retry.py",
                        "sha": "abc123",
                        "html_url": "https://github.com/owner/repo/blob/abc123/src/retry.py",
                        "score": 7.5,
                        "text_matches": [
                            {
                                "fragment": "def retry_after():",
                                "matches": [{"text": "retry_after", "indices": [4, 15]}],
                            }
                        ],
                    }
                ],
            },
            provider="github",
            query_variant="retry",
            page=2,
            per_page=100,
            max_results=100,
        )
        self.assertEqual(total, 1)
        self.assertTrue(incomplete)
        self.assertEqual(hits[0].search_rank, 101)
        self.assertEqual(hits[0].fragments[0].text, "def retry_after():")
        self.assertEqual(hits[0].match_spans[0]["start"], 4)
        self.assertEqual(hits[0].sha, "abc123")
        self.assertEqual(hits[0].result_kind, "code_match")
        self.assertEqual(hits[0].location.precision, "file")
        self.assertEqual(hits[0].location.revision, "abc123")
        self.assertTrue(hits[0].location.revision_available)
        self.assertTrue(hits[0].location.match_data_available)

    async def test_deep_search_respects_ten_page_1000_result_guard(self):
        def page_response():
            page = len(client.get_calls) + 1
            items = [
                {
                    "repository": {"full_name": "owner/repo"},
                    "path": f"src/file_{page}_{index}.py",
                    "sha": "abc123",
                    "html_url": f"https://github.com/owner/repo/blob/abc123/src/file_{page}_{index}.py",
                }
                for index in range(100)
            ]
            return FakeResponse({"total_count": 2500, "incomplete_results": False, "items": items})

        client = FakeClient(get_responses=[page_response for _ in range(10)])
        request = CodeSearchRequest(query="retry", deep=True)
        with patch.dict(os.environ, {"GITHUB_TOKEN": "token"}):
            hits, diagnostics = await _search_scope_variant(
                client,
                request,
                build_query_plan("retry"),
                "token",
                "owner/repo",
                "retry",
                _RequestGate(20),
            )
        self.assertEqual(len(client.get_calls), 10)
        self.assertEqual(len(hits), 1000)
        self.assertEqual(diagnostics, [])

    async def test_scoped_zero_probe_reports_not_found(self):
        client = FakeClient(get_responses=[FakeResponse({}, status_code=404)])
        gate = _RequestGate(2)
        diagnostic = await _probe_repo_state(client, "token", "owner/missing", "retry", gate)
        self.assertIsNotNone(diagnostic)
        self.assertEqual(diagnostic.failure_kind, "not_found")


class TestGrepAppAdapter(IsolatedAsyncioTestCase):
    async def test_exception_group_exposes_nested_http_status(self):
        request = httpx.Request("POST", "https://mcp.grep.app")
        response = httpx.Response(504, request=request)
        nested = ExceptionGroup(
            "task group",
            [httpx.HTTPStatusError("gateway timeout", request=request, response=response)],
        )
        self.assertEqual(
            _exception_chain(nested),
            [
                {
                    "type": "HTTPStatusError",
                    "message": "gateway timeout",
                    "status_code": 504,
                    "url": "https://mcp.grep.app",
                }
            ],
        )

    async def test_alias_hydration_is_commit_pinned(self):
        client = FakeClient(
            post_responses=[
                FakeResponse(
                    {
                        "data": {
                            "f0": {
                                "object": {
                                    "oid": "blob123",
                                    "byteSize": 52,
                                    "isBinary": False,
                                    "text": "class App:\n    def run(self):\n        return helper()\n",
                                }
                            }
                        }
                    }
                )
            ]
        )
        hit = CodeSearchHit(
            repository="owner/repo",
            path="src/retry.py",
            sha="blob123",
            commit_oid="commit123",
            url="https://github.com/owner/repo/blob/commit123/src/retry.py",
            provider="github",
            fragments=[TextFragment(text="helper()")],
        )
        diagnostics, hydrated, truncated = await hydrate_github_hits(
            [hit],
            http_client=cast(httpx.AsyncClient, client),
            token="token",
            max_chars_per_file=12,
        )
        self.assertEqual(hydrated, 1)
        self.assertFalse(truncated)  # No server-side truncation
        self.assertEqual(hit.commit_oid, "commit123")
        # Full source preserved — no char-cap
        self.assertIn("return helper()", hit.hydrated_source or "")
        ast_payload = hit.source_metadata["ast_classification"]
        if ast_payload["status"] == "ok":
            self.assertTrue(
                {item["role"] for item in ast_payload["evidence"]}
                & {"definition", "callsite"}
            )
        else:
            self.assertIn(ast_payload["status"], ("parser_unavailable", "grammar_not_cached"))
        self.assertIn("class App:", hit.hydrated_source or "")
        self.assertIn(
            "commit123:src/retry.py", client.post_calls[0][1]["json"]["variables"].values()
        )


class TestSourcegraphAndGrepApp(IsolatedAsyncioTestCase):
    async def test_sourcegraph_v3_parser_preserves_line_numbers_and_limit_diagnostic(self):
        hits, diagnostics = _parse_payload(
            {
                "data": {
                    "search": {
                        "results": {
                            "matchCount": 1,
                            "limitHit": True,
                            "results": [
                                {
                                    "__typename": "FileMatch",
                                    "repository": {
                                        "name": "github.com/owner/repo",
                                        "url": "https://sourcegraph.com/github.com/owner/repo",
                                    },
                                    "file": {
                                        "path": "src/retry.py",
                                        "url": "/github.com/owner/repo/-/blob/src/retry.py",
                                    },
                                    "lineMatches": [
                                        {
                                            "preview": "retry_after()",
                                            "lineNumber": 42,
                                            "offsetAndLengths": [[0, 5]],
                                        }
                                    ],
                                    "symbols": [{"name": "retry_after", "kind": "FUNCTION"}],
                                }
                            ],
                        }
                    }
                }
            },
            query_variant="retry",
            max_results=10,
        )
        self.assertEqual(hits[0].repository, "owner/repo")
        self.assertEqual(hits[0].line_start, 42)
        self.assertEqual(hits[0].symbols[0]["name"], "retry_after")
        self.assertEqual(hits[0].match_spans[0]["line"], 42)
        self.assertEqual(hits[0].result_kind, "code_match")
        self.assertEqual(diagnostics[0].failure_kind, "incomplete_index")
        self.assertEqual(hits[0].line_end, 42)
        self.assertEqual(hits[0].location.precision, "line")
        self.assertTrue(hits[0].location.lines_available)
        self.assertTrue(hits[0].location.match_data_available)

    async def test_grepapp_parser_reads_verified_human_blocks(self):
        hits = parse_grepapp_text(
            """Repository: owner/repo
Path: src/retry.py
URL: https://github.com/owner/repo/blob/main/src/retry.py
License: MIT
42 | retry_after()
43 | return response
""",
            query_variant="retry",
            max_results=5,
        )
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].line_start, 42)
        self.assertEqual(hits[0].line_end, 43)
        self.assertEqual(hits[0].location.precision, "line")
        self.assertTrue(hits[0].location.lines_available)
        self.assertTrue(hits[0].location.match_data_available)
        self.assertEqual(hits[0].repository, "owner/repo")
        self.assertIn("return response", hits[0].snippet or "")


    async def test_grepapp_rest_branch_is_not_revision(self):
        hits = _parse_rest_payload(
            {
                "hits": {
                    "hits": [
                        {
                            "repo": "owner/repo",
                            "path": "src/retry.py",
                            "branch": "main",
                            "content": {"snippet": "<em>retry</em>"},
                        }
                    ]
                }
            },
            query_variant="retry",
            max_results=5,
        )
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].result_kind, "code_match")
        self.assertIsNone(hits[0].commit_oid)
        self.assertEqual(hits[0].location.ref, "main")
        self.assertEqual(hits[0].source_metadata["branch"], "main")
        self.assertEqual(hits[0].location.precision, "file")
        self.assertFalse(hits[0].location.revision_available)

class TestExaAndDocs(IsolatedAsyncioTestCase):
    async def test_exa_uses_context_endpoint_and_parses_github_blob(self):
        # Exa integration uses the /context endpoint (httpx POST), not exa_py.
        client = FakeClient(
            post_responses=[
                FakeResponse(
                    {
                        "response": (
                            "Retry with exponential backoff. See "
                            "https://github.com/owner/repo/blob/main/retry.py"
                        ),
                        "query": "retry backoff implementation",
                        "resultsCount": 3,
                    }
                )
            ]
        )
        request = CodeSearchRequest(query="retry")
        with patch.dict(os.environ, {"EXA_API_KEY": "key"}):
            result = await search_exa(build_query_plan("retry"), request, http_client=client)
        self.assertEqual(len(client.post_calls), 1)
        self.assertEqual(client.post_calls[0][0], "https://api.exa.ai/context")
        self.assertEqual(result.hits[0].repository, "owner/repo")
        self.assertEqual(result.hits[0].result_kind, "semantic_page")
        self.assertEqual(result.hits[0].location.precision, "file")
        self.assertEqual(result.hits[0].location.ref, "main")
        self.assertFalse(result.hits[0].location.lines_available)
        self.assertFalse(result.hits[0].location.revision_available)
        self.assertFalse(result.hits[0].location.match_data_available)

    async def test_exa_reports_missing_key(self):
        with patch.dict(os.environ, {"EXA_API_KEY": ""}):
            result = await search_exa(
                build_query_plan("where is retry backoff implemented"),
                CodeSearchRequest(query="where is retry backoff implemented"),
                http_client=FakeClient(),
            )
        self.assertEqual(result.diagnostics[0].failure_kind, "auth")
        self.assertEqual(result.diagnostics[0].outcome, "partial")

    async def test_context7_search_then_fetch_parses_text_response(self):
        client = FakeClient(
            get_responses=[
                FakeResponse({"results": [{"id": "/facebook/react", "title": "React"}]}),
                FakeResponse({}, text="useEffect(() => {}, [])"),
            ]
        )
        request = CodeSearchRequest(
            query="hooks",
            repo_name="facebook/react",
            library_name="/facebook/react",
            topic="hooks",
        )
        result = await search_context7(build_query_plan("hooks"), request, http_client=client)
        self.assertEqual(result.hits[0].source_metadata["library_id"], "/facebook/react")
        self.assertIn("useEffect", result.hits[0].snippet or "")
        self.assertEqual(result.hits[0].result_kind, "documentation")
        self.assertEqual(result.hits[0].location.precision, "url")
        self.assertEqual(len(client.get_calls), 2)

    async def test_deepwiki_mcp_answer_maps_to_typed_hit(self):
        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def initialize(self):
                return None

            async def call_tool(self, name, arguments):
                self.name = name
                self.arguments = arguments
                return SimpleNamespace(
                    content=[SimpleNamespace(text="Use the retry helper")], isError=False
                )

        @asynccontextmanager
        async def fake_transport(url):
            yield object(), object(), None

        request = CodeSearchRequest(
            query="retry", repo_name="zamalali/DeepGit", library_name="DeepGit"
        )
        with (
            patch(
                "kindly_web_search_mcp_server.tools.code_search.docs.streamable_http_client",
                fake_transport,
            ),
            patch(
                "kindly_web_search_mcp_server.tools.code_search.docs.ClientSession",
                return_value=FakeSession(),
            ),
        ):
            result = await search_deepwiki(build_query_plan("retry"), request)
        self.assertEqual(result.hits[0].provider, "deepwiki")
        self.assertIn("retry helper", result.hits[0].snippet or "")
        self.assertEqual(result.hits[0].result_kind, "documentation")
        self.assertEqual(result.hits[0].location.precision, "url")


class TestRankingAndReranking(IsolatedAsyncioTestCase):
    async def test_rrf_deduplicates_and_records_evidence_components(self):
        hits = [
            CodeSearchHit(
                repository="owner/repo",
                path="src/retry.py",
                url="https://github.com/owner/repo/blob/main/src/retry.py",
                provider="github",
                query_variant="retry",
                search_rank=1,
                snippet="retry-after",
            ),
            CodeSearchHit(
                repository="owner/repo",
                path="src/retry.py",
                url="https://sourcegraph.com/retry",
                provider="sourcegraph",
                query_variant="retry",
                search_rank=2,
                snippet="retry-after",
            ),
        ]
        ranked = rank_hits(build_query_plan("retry"), hits, max_results=10)
        self.assertEqual(len(ranked), 1)
        self.assertIn("rrf", ranked[0].score_components)
        self.assertGreater(ranked[0].score_components["provider_agreement"], 0)

    async def test_cloud_rerank_maps_indices_and_blends_rrf_score(self):
        hits = [
            CodeSearchHit(url="https://a.example", provider="github", score=0.4, snippet="a"),
            CodeSearchHit(url="https://b.example", provider="github", score=0.2, snippet="b"),
        ]
        outcome = SimpleNamespace(
            provider_id="cohere_fast",
            model="rerank-v4.0-fast",
            ranked=[RerankResult(index=1, score=0.9), RerankResult(index=0, score=0.1)],
            ordered_candidates=[],
            error=None,
        )
        with patch(
            "kindly_web_search_mcp_server.tools.code_search.reranking.rerank_with_provider_fallback",
            AsyncMock(return_value=outcome),
        ):
            result = await rerank_code_hits("retry", hits, max_candidates=2, max_results=2)
        # Blended score (weight 0.20): a = 0.8*1.0 + 0.2*0.0 = 0.8, b = 0.8*0.0 + 0.2*1.0 = 0.2.
        self.assertEqual(
            [item.url for item in result.hits[:2]], ["https://a.example", "https://b.example"]
        )
        self.assertEqual(result.hits[0].score, 0.8)
        self.assertEqual(result.hits[1].score, 0.2)
        self.assertEqual(result.hits[0].score_components["cloud_rerank_norm"], 0.0)
        self.assertEqual(result.hits[1].score_components["cloud_rerank_norm"], 1.0)
        self.assertEqual(result.hits[0].score_components["cloud_rerank_provider"], "cohere_fast")
        self.assertEqual(result.hits[0].score_components["cloud_rerank_model"], "rerank-v4.0-fast")
        self.assertEqual(result.metadata["blend_weight"], 0.20)

    async def test_cloud_rerank_exhaustion_fails_open(self):
        hit = CodeSearchHit(url="https://a.example", provider="github", score=0.4)
        outcome = SimpleNamespace(
            provider_id="chain",
            model=None,
            ranked=[],
            ordered_candidates=[hit],
            error=RuntimeError("no providers"),
        )
        with patch(
            "kindly_web_search_mcp_server.tools.code_search.reranking.rerank_with_provider_fallback",
            AsyncMock(return_value=outcome),
        ):
            result = await rerank_code_hits("retry", [hit])
        self.assertEqual(result.hits[0].score, 0.4)
        self.assertIsNotNone(result.diagnostic)
        self.assertEqual(result.diagnostic.outcome, "partial")

    async def test_no_truncation_all_hits_preserved(self):
        hits = [
            CodeSearchHit(url=f"https://{index}.example", provider="exa", snippet="x" * 200)
            for index in range(5)
        ]
        # No compact_hits — all hits survive; clients handle clipping.
        self.assertEqual(len(hits), 5)
        self.assertTrue(all(item.snippet for item in hits))

    async def test_hydrated_source_preserved_in_public_file(self):
        hit = CodeSearchHit(
            url="https://github.com/acme/lib/blob/main/src/retry.py",
            provider="github",
            repository="acme/lib",
            path="src/retry.py",
            hydrated_source="def retry():\n    return 1\n",
            score_components={"rrf": 0.01, "cloud_rerank_score": 0.9},
            source_metadata={"cloud_rerank_provider": "cohere_fast", "providers": ["github"]},
            reasons=["cloud rerank: cohere_fast"],
        )
        public = to_public_file(hit)
        dumped = public.model_dump()
        self.assertNotIn("score_components", dumped)
        self.assertNotIn("source_metadata", dumped)
        self.assertIn("def retry", public.text_matches[0])


class TestDispatchAndValidation(IsolatedAsyncioTestCase):
    async def test_backend_selects_code_and_semantic_channels(self):
        response = ProviderResponse(
            provider="exa", hits=[CodeSearchHit(url="https://a.example", provider="exa")]
        )
        with (
            patch(
                "kindly_web_search_mcp_server.tools.code_search.orchestrator.search_exa",
                AsyncMock(return_value=response),
            ) as exa,
            patch(
                "kindly_web_search_mcp_server.tools.code_search.orchestrator.search_github",
                AsyncMock(return_value=ProviderResponse(provider="github")),
            ) as github,
            patch(
                "kindly_web_search_mcp_server.tools.code_search.orchestrator.search_sourcegraph",
                AsyncMock(return_value=ProviderResponse(provider="sourcegraph")),
            ) as sourcegraph,
            patch(
                "kindly_web_search_mcp_server.tools.code_search.orchestrator.search_grepapp",
                AsyncMock(return_value=ProviderResponse(provider="grep.app")),
            ) as grepapp,
        ):
            result = await execute_code_search(
                CodeSearchRequest(query="where is retry backoff implemented"),
                build_query_plan("where is retry backoff implemented"),
                http_client=FakeClient(),
            )
        self.assertEqual(result.results[0].provider, "exa")
        self.assertEqual(result.results[0].result_kind, "semantic_page")
        self.assertEqual(result.results[0].location.precision, "url")
        exa.assert_awaited_once()
        github.assert_awaited_once()
        sourcegraph.assert_awaited_once()
        grepapp.assert_awaited_once()

    async def test_docs_identifiers_are_independently_optional(self):
        request = _validate_request(
            query="hooks",
            repositories=None,
            language=None,
            path=None,
            filename=None,
            extension=None,
            regexp=False,
            deep=False,
            repo_name="facebook/react",
            library_name=None,
            topic=None,
        )
        self.assertEqual(request.repo_name, "facebook/react")
        self.assertIsNone(request.library_name)


class TestPublicCodeSearchOutput(IsolatedAsyncioTestCase):
    def test_public_file_carries_provider_strengths(self) -> None:
        hit = CodeSearchHit(
            url="https://github.com/langfuse/langfuse/blob/abc/packages/db/schema.ts",
            provider="github",
            repository="langfuse/langfuse",
            path="packages/db/schema.ts",
            sha="abc123",
            line_start=10,
            line_end=18,
            hydrated_source="export const traces = pgTable('traces', {\n  id: text('id'),\n});",
            fragments=[TextFragment(text="export const traces = pgTable", line_start=10, line_end=10)],
            score=0.91,
            score_components={"rrf": 0.02, "cloud_rerank_norm": 0.8},
            reasons=["cloud rerank: cohere_fast"],
            source_metadata={
                "cloud_rerank_provider": "cohere_fast",
                "providers": ["github", "grep.app"],
                "source_window_start": 10,
                "source_window_end": 18,
            },
        )
        public = to_public_file(hit)
        dumped = public.model_dump()
        self.assertNotIn("score_components", dumped)
        self.assertNotIn("source_metadata", dumped)
        self.assertNotIn("reasons", dumped)
        self.assertEqual(public.path, "packages/db/schema.ts")
        self.assertEqual(public.language, "TypeScript")
        self.assertEqual(public.sha, "abc123")
        self.assertIn("pgTable", public.text_matches[0])
        # match_lines parallel to text_matches
        self.assertEqual(len(public.text_matches), len(public.match_lines))

    def test_public_result_groups_by_repository_and_emits_hints(self) -> None:
        hit = CodeSearchHit(
            url="https://github.com/acme/lib/blob/main/src/a.py",
            provider="github",
            repository="acme/lib",
            path="src/a.py",
            snippet="def retry():\n    pass",
        )
        internal = CodeSearchResultType(
            query="retry",
            outcome="partial",
            results=[hit],
            repositories=[],
            diagnostics=[
                Diagnostic(
                    provider="sourcegraph",
                    outcome="partial",
                    message="Sourcegraph limit: result limit hit",
                    failure_kind="incomplete_index",
                ),
                Diagnostic(
                    provider="sourcegraph",
                    outcome="partial",
                    message="Sourcegraph limit: result limit hit again",
                    failure_kind="incomplete_index",
                ),
            ],
            stats=Stats(returned_count=1),
            query_metadata=QueryMetadata(
                original_query="retry",
                mode="code",
                compiled_queries={"github": ["retry"]},
            ),
        )
        public = to_public_result(internal)
        dumped = public.model_dump()
        self.assertNotIn("diagnostics", dumped)
        self.assertNotIn("stats", dumped)
        self.assertNotIn("query_metadata", dumped)
        self.assertNotIn("truncated", dumped)
        self.assertNotIn("more_results", dumped)
        # Grouped: results[0] is a group with files[]
        self.assertEqual(public.results[0].repository, "acme/lib")
        self.assertEqual(public.results[0].owner, "acme")
        self.assertEqual(public.results[0].repo, "lib")
        self.assertEqual(public.results[0].files[0].path, "src/a.py")
        self.assertEqual(public.results[0].files[0].text_matches, ["def retry():\n    pass"])
        # Hints present for incomplete index
        self.assertTrue(any(h.code == "incomplete_index" for h in public.hints))
        self.assertNotIn("warnings", dumped)
        self.assertNotIn("returned_count", dumped)
        self.assertEqual(public.agent_ready_count, 0)
        self.assertEqual(public.agent_ready_evidence_rate, 0.0)
        self.assertTrue(public.incomplete_results)

    def test_public_file_exposes_agent_ready_projection(self) -> None:
        hit = CodeSearchHit(
            url="https://github.com/org/repo/blob/" + "a" * 40 + "/src/app.py",
            provider="sourcegraph",
            repository="org/repo",
            path="src/app.py",
            commit_oid="a" * 40,
            line_start=12,
            line_end=18,
            snippet="def run():\n    return True",
        )
        public = to_public_file(hit)

        self.assertTrue(public.agent_ready)
        self.assertEqual(public.agent_ready_fail_reasons, [])
        self.assertEqual(public.line_start, 12)
        self.assertEqual(public.line_end, 18)
        self.assertEqual(public.providers, ["sourcegraph"])
        self.assertEqual(public.snippet, public.text_matches[0])

    def test_public_projection_does_not_shorten_provider_text(self) -> None:
        source = "SELECT value FROM result_labels WHERE ranking_position = 1;\n" * 500
        hit = CodeSearchHit(
            url="https://github.com/org/repo/blob/main/schema.sql",
            provider="github",
            repository="org/repo",
            path="schema.sql",
            line_start=1,
            line_end=500,
            fragments=[TextFragment(text=source, line_start=1, line_end=500)],
        )
        public = to_public_file(hit)

        self.assertEqual(public.text_matches, [source])
        self.assertEqual(public.snippet, source)
        self.assertNotIn("truncated", public.model_dump())

    def test_unready_file_exposes_reasons_instead_of_legacy_warning_dump(self) -> None:
        public = to_public_file(CodeSearchHit(provider="github", path="schema.sql"))

        self.assertFalse(public.agent_ready)
        self.assertIn("missing_url", public.agent_ready_fail_reasons)
        self.assertIn("insufficient_text_context", public.agent_ready_fail_reasons)
        self.assertIn("missing_lines_or_revision", public.agent_ready_fail_reasons)
