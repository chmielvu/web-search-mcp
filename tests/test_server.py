from __future__ import annotations

import sys
import os
from pathlib import Path
import unittest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kindly_web_search_mcp_server.models import WebSearchResponse, WebSearchResult


class TestWebSearchTool(unittest.IsolatedAsyncioTestCase):
    def test_core_tools_expose_structured_output_schemas(self) -> None:
        os.environ.pop("TOOL_PROFILE", None)
        os.environ.pop("TOOL_SEARCH_ENABLED", None)
        sys.modules.pop("kindly_web_search_mcp_server.server", None)
        sys.modules.pop("kindly_web_search_mcp_server.settings", None)
        from kindly_web_search_mcp_server.server import mcp

        tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}

        self.assertIn("web_search", tools)
        self.assertIn("get_content", tools)
        self.assertIn("batch_get_content", tools)
        self.assertIn("generate_sitemap", tools)
        self.assertNotIn("generate_semantic_sitemap", tools)
        self.assertIn("youtube_transcript", tools)
        self.assertIn("youtube_search", tools)

        web_schema = str(tools["web_search"].output_schema)
        self.assertIn("query", web_schema)
        self.assertIn("results", web_schema)

        get_content_schema = str(tools["get_content"].output_schema)
        self.assertIn("input_url", get_content_schema)
        self.assertIn("page_content", get_content_schema)
        self.assertIn("window", get_content_schema)

        batch_schema = str(tools["batch_get_content"].output_schema)
        self.assertIn("results", batch_schema)
        self.assertIn("total_requested", batch_schema)
        self.assertIn("cursor", batch_schema)
        self.assertIn("summary", batch_schema)

        transcript_schema = str(tools["youtube_transcript"].output_schema)
        self.assertIn("video_id", transcript_schema)
        self.assertIn("transcript_text", transcript_schema)

        youtube_search_schema = str(tools["youtube_search"].output_schema)
        self.assertIn("query", youtube_search_schema)
        self.assertIn("total_results", youtube_search_schema)

    def test_public_resource_list_includes_native_resources(self) -> None:
        from kindly_web_search_mcp_server.server import mcp

        resources = asyncio.run(mcp.list_resources())
        uris = {str(getattr(resource, "uri", "")) for resource in resources}

        self.assertIn("status://providers", uris)
        self.assertIn("status://features", uris)
        self.assertIn("docs://workflow", uris)
        self.assertIn("settings://public", uris)

        self.assertIn("analytics://schema", uris)
        self.assertIn("analytics://candidate-survival", uris)

    def test_public_read_resource_reads_native_resources(self) -> None:
        from kindly_web_search_mcp_server.server import mcp

        settings_result = asyncio.run(mcp.read_resource("settings://public"))
        schema_result = asyncio.run(mcp.read_resource("analytics://schema"))

        self.assertIn("query_understanding", str(settings_result))
        self.assertIn("search_runs", str(schema_result))

    def test_features_status_reports_personal_enhanced_flags(self) -> None:
        from kindly_web_search_mcp_server.server import mcp

        features_result = asyncio.run(mcp.read_resource("status://features"))

        self.assertIn("Personal Enhanced Profile", str(features_result))
        self.assertIn("Entity Extraction", str(features_result))
        self.assertIn("Entity Overlap Rerank", str(features_result))
        self.assertIn("Result Memory", str(features_result))

    def test_analytics_report_resources_use_report_catalog(self) -> None:
        from kindly_web_search_mcp_server.tools.resources import (
            get_analytics_report_resource,
            get_candidate_survival_resource,
        )

        report_table = pa.table(
            {
                "stage": ["merged"],
                "rows": [12],
            }
        )

        with patch(
            "kindly_web_search_mcp_server.analytics.reports.run_report",
            return_value=report_table,
        ) as run_report_mock:
            candidate_result = get_candidate_survival_resource()
            template_result = get_analytics_report_resource("provider-performance", days=14)

        self.assertEqual(run_report_mock.call_args_list[0].args, ("candidate-survival",))
        self.assertEqual(run_report_mock.call_args_list[0].kwargs, {"days": 7})
        self.assertEqual(
            run_report_mock.call_args_list[1].args,
            ("provider-performance",),
        )
        self.assertEqual(run_report_mock.call_args_list[1].kwargs, {"days": 14})
        self.assertIn('"days": 7', str(candidate_result))
        self.assertIn('"days": 14', str(template_result))

    def test_public_resource_template_list_includes_native_templates(self) -> None:
        from kindly_web_search_mcp_server.server import mcp

        templates = asyncio.run(mcp.list_resource_templates())
        uri_templates = {str(getattr(template, "uri_template", "")) for template in templates}

        self.assertIn("analytics://reports/{report_name}", uri_templates)

    def test_public_prompt_list_includes_native_prompts(self) -> None:
        from kindly_web_search_mcp_server.server import mcp

        prompts = asyncio.run(mcp.list_prompts())
        names = {getattr(prompt, "name", "") for prompt in prompts}

        self.assertEqual(names, {"web_search_workflow", "query_refinement"})

    def test_public_render_prompt_renders_native_prompts(self) -> None:
        from kindly_web_search_mcp_server.server import mcp

        workflow_result = asyncio.run(
            mcp.render_prompt("web_search_workflow", {"query": "test query"})
        )

        self.assertIn("test query", str(workflow_result))

    def test_tool_timeout_budget_can_exceed_55_seconds(self) -> None:
        from kindly_web_search_mcp_server.server import (
            _resolve_tool_total_timeout_seconds,
        )

        with patch.dict(
            os.environ,
            {
                "TOOL_TOTAL_TIMEOUT_SECONDS": "120",
                "TOOL_TOTAL_TIMEOUT_MAX_SECONDS": "600",
            },
            clear=False,
        ):
            self.assertEqual(_resolve_tool_total_timeout_seconds(), 120.0)

        with patch.dict(
            os.environ,
            {
                "TOOL_TOTAL_TIMEOUT_SECONDS": "120",
                "TOOL_TOTAL_TIMEOUT_MAX_SECONDS": "100",
            },
            clear=False,
        ):
            self.assertEqual(_resolve_tool_total_timeout_seconds(), 100.0)

        with patch.dict(
            os.environ,
            {"TOOL_TOTAL_TIMEOUT_SECONDS": "abc"},
            clear=False,
        ):
            self.assertEqual(_resolve_tool_total_timeout_seconds(), 120.0)

        with patch.dict(
            os.environ,
            {"TOOL_TOTAL_TIMEOUT_MAX_SECONDS": "abc"},
            clear=False,
        ):
            self.assertEqual(_resolve_tool_total_timeout_seconds(), 120.0)

        with patch.dict(
            os.environ,
            {"TOOL_TOTAL_TIMEOUT_MAX_SECONDS": "90"},
            clear=False,
        ):
            self.assertEqual(_resolve_tool_total_timeout_seconds(), 90.0)

    def test_web_search_concurrency_defaults_on_windows(self) -> None:
        from kindly_web_search_mcp_server.server import (
            _resolve_web_search_max_concurrency,
        )

        with (
            patch.dict(os.environ, {}, clear=True),
            patch("kindly_web_search_mcp_server.server.os.name", "nt"),
        ):
            self.assertEqual(_resolve_web_search_max_concurrency(3), 1)

        with (
            patch.dict(
                os.environ,
                {"WEB_SEARCH_MAX_CONCURRENCY": "3"},
                clear=True,
            ),
            patch("kindly_web_search_mcp_server.server.os.name", "nt"),
        ):
            self.assertEqual(_resolve_web_search_max_concurrency(3), 3)

        with (
            patch.dict(
                os.environ,
                {"WEB_SEARCH_MAX_CONCURRENCY": "abc"},
                clear=True,
            ),
            patch("kindly_web_search_mcp_server.server.os.name", "nt"),
        ):
            self.assertEqual(_resolve_web_search_max_concurrency(3), 1)

        with (
            patch.dict(
                os.environ,
                {"WEB_SEARCH_MAX_CONCURRENCY": "0"},
                clear=True,
            ),
            patch("kindly_web_search_mcp_server.server.os.name", "nt"),
        ):
            self.assertEqual(_resolve_web_search_max_concurrency(3), 1)

        with (
            patch.dict(
                os.environ,
                {"WEB_SEARCH_MAX_CONCURRENCY": "-2"},
                clear=True,
            ),
            patch("kindly_web_search_mcp_server.server.os.name", "nt"),
        ):
            self.assertEqual(_resolve_web_search_max_concurrency(3), 1)

    def test_web_search_concurrency_limited_by_num_results_on_windows(self) -> None:
        from kindly_web_search_mcp_server.server import (
            _resolve_web_search_max_concurrency,
        )

        with (
            patch.dict(
                os.environ,
                {"WEB_SEARCH_MAX_CONCURRENCY": "10"},
                clear=True,
            ),
            patch("kindly_web_search_mcp_server.server.os.name", "nt"),
        ):
            self.assertEqual(_resolve_web_search_max_concurrency(3), 3)

    def test_web_search_concurrency_defaults_on_non_windows(self) -> None:
        from kindly_web_search_mcp_server.server import (
            _resolve_web_search_max_concurrency,
        )

        with (
            patch.dict(os.environ, {}, clear=True),
            patch("kindly_web_search_mcp_server.server.os.name", "posix"),
        ):
            self.assertEqual(_resolve_web_search_max_concurrency(3), 3)

        with (
            patch.dict(
                os.environ,
                {"WEB_SEARCH_MAX_CONCURRENCY": "5"},
                clear=True,
            ),
            patch("kindly_web_search_mcp_server.server.os.name", "posix"),
        ):
            self.assertEqual(_resolve_web_search_max_concurrency(3), 3)

        with (
            patch.dict(
                os.environ,
                {"WEB_SEARCH_MAX_CONCURRENCY": "7"},
                clear=True,
            ),
            patch("kindly_web_search_mcp_server.server.os.name", "posix"),
        ):
            self.assertEqual(_resolve_web_search_max_concurrency(5), 5)

        with (
            patch.dict(
                os.environ,
                {"WEB_SEARCH_MAX_CONCURRENCY": "abc"},
                clear=True,
            ),
            patch("kindly_web_search_mcp_server.server.os.name", "posix"),
        ):
            self.assertEqual(_resolve_web_search_max_concurrency(3), 3)

    def test_tool_timeout_defaults_to_120_seconds(self) -> None:
        from kindly_web_search_mcp_server.server import (
            _resolve_tool_total_timeout_seconds,
        )

        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_resolve_tool_total_timeout_seconds(), 120.0)

    async def test_web_search_returns_results(self) -> None:
        from kindly_web_search_mcp_server.server import web_search

        mocked_results = [WebSearchResult(title="T", link="https://example.com", snippet="S")]

        # Create mock context with .info() method
        mock_ctx = AsyncMock()
        mock_ctx.info = AsyncMock()

        with patch(
            "kindly_web_search_mcp_server.search.service.execute_web_search", new_callable=AsyncMock
        ) as mock_search:
            mock_search.return_value = WebSearchResponse(query="hello", results=mocked_results)

            # Access underlying function via .fn attribute (FastMCP v2 returns FunctionTool)
            tool_fn = web_search.fn if hasattr(web_search, "fn") else web_search
            out = await tool_fn("hello", "Find information about hello", ctx=mock_ctx)

        self.assertIsInstance(out, dict)
        self.assertEqual(out["query"], "hello")
        self.assertIn("results", out)
        self.assertEqual(len(out["results"]), 1)
        self.assertEqual(out["results"][0]["title"], "T")
        self.assertEqual(out["results"][0]["link"], "https://example.com")
        self.assertEqual(out["results"][0]["snippet"], "S")
        self.assertNotIn("page_content", out["results"][0])

    async def test_web_search_forwards_search_options(self) -> None:
        from kindly_web_search_mcp_server.server import web_search
        from kindly_web_search_mcp_server.search.options import SearchOptions

        mocked_results = [WebSearchResult(title="T", link="https://example.com", snippet="S")]

        mock_ctx = AsyncMock()
        mock_ctx.info = AsyncMock()

        with patch(
            "kindly_web_search_mcp_server.search.service.execute_web_search",
            new_callable=AsyncMock,
        ) as mock_search:
            mock_search.return_value = WebSearchResponse(
                query="hello",
                results=mocked_results,
            )

            tool_fn = web_search.fn if hasattr(web_search, "fn") else web_search
            out = await tool_fn(
                "hello",
                "Find information about hello",
                site_filters=["docs.example.com"],
                domain_filters=["example.com"],
                ctx=mock_ctx,
            )

        forwarded_request = mock_search.call_args[0][0]
        forwarded_options = forwarded_request.options
        self.assertIsInstance(forwarded_options, SearchOptions)
        self.assertEqual(forwarded_options.site_filters, ("docs.example.com", "example.com"))
        self.assertNotIn("result_window", out)

    async def test_get_content_returns_markdown(self) -> None:
        from kindly_web_search_mcp_server.content.artifact import ContentArtifact
        from kindly_web_search_mcp_server.server import get_content

        mock_ctx = AsyncMock()
        mock_ctx.info = AsyncMock()

        with (
            patch(
                "kindly_web_search_mcp_server.tools.content.fetch_content_artifact",
                new_callable=AsyncMock,
            ) as mock_fetch,
            patch(
                "kindly_web_search_mcp_server.tools.content.get_page_cache"
            ) as mock_get_page_cache,
        ):
            mock_page_cache = AsyncMock()
            mock_page_cache.lookup.return_value = None
            mock_page_cache.alookup.return_value = None
            mock_page_cache.store = MagicMock()
            mock_get_page_cache.return_value = mock_page_cache

            mock_fetch.return_value = ContentArtifact(
                input_url="https://example.com",
                normalized_url="https://example.com",
                fetched_url="https://example.com/",
                status="success",
                source_type="html",
                fetch_backend="test",
                content_type="text/markdown",
                markdown="# Title\n\nHello",
            )
            tool_fn = get_content.fn if hasattr(get_content, "fn") else get_content
            out = await tool_fn("https://example.com", ctx=mock_ctx)

        self.assertEqual(out["url"], "https://example.com/")
        self.assertNotIn("input_url", out)
        self.assertNotIn("normalized_url", out)
        self.assertNotIn("fetched_url", out)
        self.assertEqual(out["source_type"], "html")
        self.assertEqual(out["fetch_backend"], "test")
        self.assertIn("page_content", out)
        self.assertIn("Hello", out["page_content"])
        self.assertEqual(out["window"]["total_chars"], len("# Title\n\nHello"))

    async def test_get_content_includes_metadata_and_links_when_requested(self) -> None:
        from kindly_web_search_mcp_server.content.artifact import ContentArtifact
        from kindly_web_search_mcp_server.server import get_content

        mock_ctx = AsyncMock()
        mock_ctx.info = AsyncMock()

        with (
            patch(
                "kindly_web_search_mcp_server.tools.content.fetch_content_artifact",
                new_callable=AsyncMock,
            ) as mock_fetch,
            patch(
                "kindly_web_search_mcp_server.tools.content.get_page_cache"
            ) as mock_get_page_cache,
        ):
            mock_page_cache = AsyncMock()
            mock_page_cache.lookup.return_value = None
            mock_page_cache.alookup.return_value = None
            mock_page_cache.store = MagicMock()
            mock_get_page_cache.return_value = mock_page_cache

            mock_fetch.return_value = ContentArtifact(
                input_url="https://example.com",
                normalized_url="https://example.com",
                fetched_url="https://example.com/",
                status="success",
                source_type="html",
                fetch_backend="test",
                content_type="text/markdown",
                markdown="First paragraph.\n\nSecond paragraph.",
                metadata={"title": "Example"},
                links=[
                    {
                        "url": "https://example.com/next",
                        "text": "Next",
                        "domain": "example.com",
                        "internal": True,
                    }
                ],
            )
            tool_fn = get_content.fn if hasattr(get_content, "fn") else get_content
            out = await tool_fn(
                "https://example.com",
                char_length=20,
                include_links=True,
                max_links=5,
                ctx=mock_ctx,
            )

        self.assertEqual(out["metadata"]["title"], "Example")
        self.assertEqual(out["links"][0]["url"], "https://example.com/next")
        self.assertIn("continuation_notice", out)
        self.assertIn("Continue at offset", out["continuation_notice"])

    async def test_get_content_returns_structured_error_artifact(self) -> None:
        from kindly_web_search_mcp_server.content.artifact import (
            ContentArtifact,
            ContentError,
        )
        from kindly_web_search_mcp_server.server import get_content

        mock_ctx = AsyncMock()
        mock_ctx.info = AsyncMock()

        with (
            patch(
                "kindly_web_search_mcp_server.tools.content.fetch_content_artifact",
                new_callable=AsyncMock,
            ) as mock_fetch,
            patch(
                "kindly_web_search_mcp_server.tools.content.get_page_cache"
            ) as mock_get_page_cache,
        ):
            mock_page_cache = AsyncMock()
            mock_page_cache.lookup.return_value = None
            mock_page_cache.alookup.return_value = None
            mock_page_cache.store = MagicMock()
            mock_get_page_cache.return_value = mock_page_cache

            mock_fetch.return_value = ContentArtifact(
                input_url="https://example.com/file.pdf",
                normalized_url="https://example.com/file.pdf",
                fetched_url=None,
                status="unsupported",
                source_type="pdf",
                fetch_backend="pdf_extract",
                content_type="application/pdf",
                markdown="",
                error=ContentError(code="pdf_extract_failed", message="bad pdf"),
            )
            tool_fn = get_content.fn if hasattr(get_content, "fn") else get_content
            out = await tool_fn("https://example.com/file.pdf", ctx=mock_ctx)

        self.assertEqual(out["url"], "https://example.com/file.pdf")
        self.assertEqual(out["status"], "unsupported")
        self.assertEqual(out["error"]["code"], "pdf_extract_failed")
        self.assertEqual(out["window"]["total_chars"], 0)

    async def test_get_content_returns_structured_timeout_error(self) -> None:
        from kindly_web_search_mcp_server.server import get_content

        mock_ctx = AsyncMock()
        mock_ctx.info = AsyncMock()

        with (
            patch(
                "kindly_web_search_mcp_server.tools.content.fetch_content_artifact",
                new_callable=AsyncMock,
            ) as mock_fetch,
            patch(
                "kindly_web_search_mcp_server.tools.content.get_page_cache"
            ) as mock_get_page_cache,
            patch(
                "kindly_web_search_mcp_server.tools.content._resolve_tool_total_timeout_seconds",
                return_value=0.01,
            ),
        ):
            mock_page_cache = AsyncMock()
            mock_page_cache.lookup.return_value = None
            mock_page_cache.alookup.return_value = None
            mock_page_cache.store = MagicMock()
            mock_get_page_cache.return_value = mock_page_cache

            mock_fetch.side_effect = asyncio.TimeoutError()
            tool_fn = get_content.fn if hasattr(get_content, "fn") else get_content
            out = await tool_fn("https://example.com", ctx=mock_ctx)

        self.assertEqual(out["status"], "error")
        self.assertEqual(out["url"], "https://example.com")
        self.assertEqual(out["error"]["code"], "timeout")

    async def test_batch_get_content_executes_local_summary_import(self) -> None:
        """Regression: line-375 local import must resolve and create_batch_summaries must run.

        Pre-fix, the local ``from ...content.summary_models import VALID_SUMMARY_MODES``
        climbed above the package root and raised ``ImportError: attempted relative
        import beyond top-level package`` before ``create_batch_summaries`` was
        awaited. Patching the orchestrator and summary creator lets the wrapper
        reach and complete the corrected import without real network calls.
        """
        from kindly_web_search_mcp_server.server import batch_get_content

        mock_ctx = AsyncMock()
        mock_ctx.info = AsyncMock()
        mock_ctx.report_progress = AsyncMock()

        fake_output = {
            "results": [
                {
                    "input_url": "https://example.com",
                    "normalized_url": "https://example.com",
                    "fetched_url": "https://example.com/",
                    "status": "success",
                    "source_type": "html",
                    "fetch_backend": "test",
                    "content_type": "text/markdown",
                    "page_content": "# Title\n\nHello",
                    "window": {"total_chars": 14, "has_more": False},
                }
            ],
            "total_requested": 1,
            "total_returned": 1,
            "total_chars_returned": 14,
            "has_more": False,
            "cursor": None,
        }

        with (
            patch(
                "kindly_web_search_mcp_server.tools.content.run_batch_fetch",
                new_callable=AsyncMock,
            ) as mock_run_batch,
            patch(
                "kindly_web_search_mcp_server.tools.content.create_batch_summaries",
                new_callable=AsyncMock,
            ) as mock_summaries,
        ):
            mock_run_batch.return_value = fake_output
            mock_summaries.return_value = [None]
            tool_fn = (
                batch_get_content.fn if hasattr(batch_get_content, "fn") else batch_get_content
            )
            out = await tool_fn(["https://example.com"], ctx=mock_ctx)

        mock_run_batch.assert_awaited_once()
        mock_summaries.assert_awaited_once()
        self.assertEqual(out["total_requested"], 1)
        self.assertEqual(out["total_returned"], 1)
        self.assertEqual(out["results"][0]["status"], "success")
        self.assertIn("Hello", out["results"][0]["page_content"])

    async def test_web_search_keeps_results_lightweight_on_cached_search(self) -> None:
        from kindly_web_search_mcp_server.server import web_search

        mocked_results = [
            WebSearchResult(
                title="T",
                link="https://example.com",
                snippet="S",
                domain="example.com",
                mime_hint="text/html",
                published_date="2026-05-29",
                source_engines=["searxng"],
                category="docs",
                raw_score=3.14,
                providers=["searxng", "ddg"],
                provider_count=2,
                score=0.92,
                diagnostics=[{"provider": "searxng"}],
            )
        ]

        # Create mock context with .info() method
        mock_ctx = AsyncMock()
        mock_ctx.info = AsyncMock()

        with patch(
            "kindly_web_search_mcp_server.search.service.execute_web_search",
            new_callable=AsyncMock,
        ) as mock_search:
            mock_search.return_value = WebSearchResponse(query="hello", results=mocked_results)
            # Access underlying function via .fn attribute (FastMCP v2 returns FunctionTool)
            tool_fn = web_search.fn if hasattr(web_search, "fn") else web_search
            out = await tool_fn("hello", "Find information about hello", ctx=mock_ctx)

        self.assertNotIn("page_content", out["results"][0])
        self.assertEqual(out["results"][0]["domain"], "example.com")
        self.assertEqual(out["results"][0]["published_date"], "2026-05-29")
        self.assertEqual(out["results"][0]["providers"], ["searxng", "ddg"])
        self.assertEqual(out["results"][0]["provider_count"], 2)
        self.assertNotIn("mime_hint", out["results"][0])
        self.assertNotIn("source_engines", out["results"][0])
        self.assertNotIn("category", out["results"][0])
        self.assertNotIn("raw_score", out["results"][0])
        self.assertNotIn("score", out["results"][0])
        self.assertNotIn("diagnostics", out["results"][0])

    async def test_discover_links_returns_links(self) -> None:
        from kindly_web_search_mcp_server.server import discover_links

        mock_ctx = AsyncMock()
        mock_ctx.info = AsyncMock()

        with patch(
            "kindly_web_search_mcp_server.tools.content.discover_page_links",
            new_callable=AsyncMock,
        ) as mock_discover:
            mock_discover.return_value = {
                "input_url": "https://example.com",
                "normalized_url": "https://example.com",
                "fetched_url": "https://example.com/",
                "source_type": "html",
                "links": [
                    {
                        "url": "https://example.com/next",
                        "text": "Next",
                        "domain": "example.com",
                        "internal": True,
                    }
                ],
                "returned_links": 1,
                "has_more": False,
                "metadata": {"title": "Example"},
            }

            tool_fn = discover_links.fn if hasattr(discover_links, "fn") else discover_links
            out = await tool_fn("https://example.com", ctx=mock_ctx)

        self.assertEqual(out["source_type"], "html")
        self.assertEqual(out["links"][0]["url"], "https://example.com/next")
        self.assertEqual(out["metadata"]["title"], "Example")

    def test_public_settings_resource_redacts_secrets(self) -> None:
        import json
        from kindly_web_search_mcp_server.server import get_public_settings_resource

        out = json.loads(get_public_settings_resource().contents[0].content)

        self.assertIn("tool_surface", out)
        self.assertIn("features", out)
        self.assertIn("providers_configured", out)
        self.assertIn("timeouts_seconds", out)
        self.assertIn("models", out)
        self.assertIn("judge_evaluation_enabled", out["features"])
        self.assertIn("judge_model", out["models"])
        self.assertIn("judge", out["timeouts_seconds"])
        self.assertNotIn("api_key", str(out).lower())
        self.assertNotIn("secret", str(out).lower())

    def test_analytics_schema_resource_exposes_object_catalog(self) -> None:
        import json
        from kindly_web_search_mcp_server.server import get_analytics_schema_resource

        out = json.loads(get_analytics_schema_resource().contents[0].content)

        self.assertGreater(out["object_count"], 0)
        self.assertIn("objects", out)

        self.assertIn("search_runs", out["objects"])
        self.assertIn("eval_cases", out["objects"])

    def test_warm_heavy_imports_loads_keyword_extract_and_router(self) -> None:
        from unittest.mock import patch

        from kindly_web_search_mcp_server.server import _warm_heavy_imports

        with patch("importlib.import_module") as mock_import:
            _warm_heavy_imports()
            self.assertEqual(mock_import.call_count, 2)
            calls = [c.args[0] for c in mock_import.call_args_list]
            self.assertIn(".search.keyword_extract", calls)
            self.assertIn(".llm.router", calls)

    def test_main_method_calls_warm_heavy_imports_before_run(self) -> None:
        from unittest.mock import patch

        from kindly_web_search_mcp_server.server import main

        events = []
        with (
            patch(
                "kindly_web_search_mcp_server.server._warm_heavy_imports",
                side_effect=lambda: events.append("warm"),
            ),
            patch("kindly_web_search_mcp_server.server.mcp") as mock_mcp,
        ):
            mock_mcp.run = lambda **kw: events.append("run")
            main(["--transport", "stdio"])
            self.assertEqual(events, ["warm", "run"])
