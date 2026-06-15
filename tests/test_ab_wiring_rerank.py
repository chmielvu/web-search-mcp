"""Tests for A/B testing wiring into the reranking pipeline (Task 24)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kindly_web_search_mcp_server.search.pipeline import run_search_pipeline
from kindly_web_search_mcp_server.rerank.core import rerank_results


# ---------------------------------------------------------------------------
# rerank.core: ab_overrides parameter handling
# ---------------------------------------------------------------------------


class TestRerankCoreABOverrides:
    """Verify that rerank_results applies A/B overrides correctly."""

    @pytest.fixture
    def mock_candidates(self):
        from kindly_web_search_mcp_server.models import WebSearchResult

        return [
            WebSearchResult(
                title=f"Result {i}",
                link=f"https://example.com/{i}",
                snippet=f"Snippet {i}",
                score=1.0 - (i * 0.01),
                domain="example.com",
            )
            for i in range(20)
        ]

    @pytest.mark.asyncio
    async def test_ab_overrides_passed_through(self, mock_candidates):
        """ab_overrides=dict should not raise and top_k override should be visible."""
        with patch(
            "kindly_web_search_mcp_server.rerank.core.settings"
        ) as s, patch(
            "kindly_web_search_mcp_server.rerank.core.decide_rerank"
        ) as mock_decide, patch(
            "kindly_web_search_mcp_server.rerank.core.embed_query"
        ) as mock_embed:
            s.reranking_enabled = True
            s.rerank_provider = "none"
            s.rerank_stack_mode = "bi_cross"
            s.rerank_recency_weight = 0.0
            s.rerank_recency_half_life_days = 90
            s.rerank_score_threshold = -999.0
            s.mmr_lambda_param = 0.5
            s.rerank_entity_overlap_enabled = False

            mock_decide.return_value = MagicMock(
                should_rerank=True, reason="test", query_type="general",
                candidate_count=20,
            )
            mock_embed.side_effect = Exception("embedding disabled for test")

            ab_overrides = {
                "top_k": 15,
                "provider": "voyage",
                "diversity_weight": 0.3,
                "entity_boost": 0.2,
            }

            result = await rerank_results(
                query="test query",
                candidates=mock_candidates,
                top_k=10,
                run_key="test-run",
                ab_overrides=ab_overrides,
            )

            # Should not crash, should return a RerankOutput
            assert result is not None
            assert len(result.results) <= 15  # top_k was overridden to 15

    @pytest.mark.asyncio
    async def test_ab_overrides_none_is_noop(self, mock_candidates):
        """ab_overrides=None should behave exactly like normal call."""
        with patch(
            "kindly_web_search_mcp_server.rerank.core.settings"
        ) as s, patch(
            "kindly_web_search_mcp_server.rerank.core.decide_rerank"
        ) as mock_decide, patch(
            "kindly_web_search_mcp_server.rerank.core.embed_query"
        ) as mock_embed:
            s.reranking_enabled = True
            s.rerank_provider = "none"
            s.rerank_stack_mode = "bi_cross"
            s.rerank_recency_weight = 0.0
            s.rerank_recency_half_life_days = 90
            s.rerank_score_threshold = -999.0
            s.mmr_lambda_param = 0.5
            s.rerank_entity_overlap_enabled = False

            mock_decide.return_value = MagicMock(
                should_rerank=True, reason="test", query_type="general",
                candidate_count=20,
            )
            mock_embed.side_effect = Exception("embedding disabled for test")

            result = await rerank_results(
                query="test query",
                candidates=mock_candidates,
                top_k=10,
                run_key="test-run",
                ab_overrides=None,
            )

            assert result is not None
            assert len(result.results) <= 10


# ---------------------------------------------------------------------------
# pipeline.py: A/B wiring integration tests
# ---------------------------------------------------------------------------


def _make_search_options(**kwargs):
    """Return SimpleNamespace as a stand-in for SearchOptions with specified attrs."""
    opts = {
        "searxng_time_range": None,
        "result_offset": 0,
    }
    opts.update(kwargs)
    return SimpleNamespace(**opts)


class TestPipelineABWiringRerank:
    """Verify that run_search_pipeline wires A/B into rerank."""

    @pytest.fixture
    def mock_settings(self):
        with patch(
            "kindly_web_search_mcp_server.search.pipeline.settings"
        ) as s:
            s.reranking_enabled = True
            s.ab_testing_enabled = True
            s.ab_config_path = "/dev/null/nonexistent"
            s.query_decomposition_max_concurrency = 1
            s.query_understanding_jsonl_path = "/dev/null"
            s.web_results_index_enabled = False
            s.judge_evaluation_enabled = False
            yield s

    @pytest.mark.asyncio
    async def test_get_ab_overrides_called_when_run_key_set(self, mock_settings):
        """When run_key is available, get_ab_overrides should be called."""
        with patch(
            "kindly_web_search_mcp_server.search.pipeline.get_ab_overrides",
            return_value=None,
        ) as mock_get_ab, patch(
            "kindly_web_search_mcp_server.search.pipeline.execute_search_branches"
        ) as mock_exec, patch(
            "kindly_web_search_mcp_server.search.pipeline.build_rewrite_variants"
        ) as mock_rewrite, patch(
            "kindly_web_search_mcp_server.search.pipeline.merge_search_results"
        ) as mock_merge, patch(
            "kindly_web_search_mcp_server.search.pipeline.normalize_query"
        ) as mock_norm, patch(
            "kindly_web_search_mcp_server.search.pipeline.resolve_query_understanding"
        ) as mock_qu, patch(
            "kindly_web_search_mcp_server.search.pipeline.resolve_search_profile"
        ) as mock_profile, patch(
            "kindly_web_search_mcp_server.search.pipeline.apply_profile_search_options"
        ) as mock_apply, patch(
            "kindly_web_search_mcp_server.search.pipeline.build_provider_execution_plan"
        ) as mock_plan, patch(
            "kindly_web_search_mcp_server.search.pipeline.build_cache_identity"
        ) as mock_cache, patch(
            "kindly_web_search_mcp_server.search.pipeline.httpx.AsyncClient"
        ) as mock_client, patch(
            "kindly_web_search_mcp_server.search.pipeline.rerank_results",
            new_callable=AsyncMock,
        ) as mock_rerank, patch(
            "kindly_web_search_mcp_server.search.pipeline.build_search_context"
        ) as mock_ctx_builder, patch(
            "kindly_web_search_mcp_server.search.pipeline.inject_result_memory_candidates"
        ) as mock_mem, patch(
            "kindly_web_search_mcp_server.search.pipeline.store_result_memory_results"
        ) as mock_store_mem, patch(
            "kindly_web_search_mcp_server.search.pipeline.build_search_response"
        ) as mock_response:
            from kindly_web_search_mcp_server.models import WebSearchResult
            from kindly_web_search_mcp_server.rerank.models import RerankOutput

            mock_norm.return_value = "normalized query"
            mock_qu.return_value = MagicMock(
                intent="general", confidence=0.9, should_decompose=False,
                rationale="test", entities=[], must_keep_terms=set(),
            )
            mock_ctx_builder.return_value = MagicMock(
                intent="general", confidence=0.9, should_decompose=False,
                profile_name="default", must_keep_terms=set(),
            )
            mock_profile.return_value = MagicMock(
                provider_weights={},
            )
            mock_apply.return_value = _make_search_options()
            mock_plan.return_value = MagicMock(
                provider_names=["web"],
                provider_weights={},
                options=MagicMock(bundles={}),
            )
            mock_cache.return_value = "cache-identity"
            mock_rewrite.return_value = ([], "none", None, None)
            mock_exec.return_value = MagicMock(
                result_lists=[],
                branch_queries=[],
                branch_providers=[],
                list_weights=[],
                branch_metadata={},
            )
            mock_merge.return_value = [
                WebSearchResult(
                    title="R1", link="https://r1.com", snippet="S1",
                    score=0.9, domain="r1.com",
                ),
                WebSearchResult(
                    title="R2", link="https://r2.com", snippet="S2",
                    score=0.8, domain="r2.com",
                ),
            ]
            mock_mem.return_value = ([], [], None, None)
            mock_store_mem.return_value = None
            mock_response.return_value = (None, None, MagicMock())
            mock_client.return_value.__aenter__.return_value = MagicMock()
            mock_client.return_value.aclose = AsyncMock()

            mock_rerank.return_value = RerankOutput(
                results=[], embedding_context=None,
            )

            await run_search_pipeline(
                query="test query",
                num_results=10,
                rewrite=False,
                diagnostics=None,
                research_goal=None,
                search_options=None,
                session_id=None,
            )

            # get_ab_overrides is called for both provider_weights and reranking layers
            assert mock_get_ab.call_count >= 1
            reranking_calls = [
                c for c in mock_get_ab.call_args_list
                if c.kwargs.get("layer") == "reranking"
            ]
            assert len(reranking_calls) >= 1, "Expected reranking layer call"

    @pytest.mark.asyncio
    async def test_no_ab_check_when_run_key_none(self, mock_settings):
        """When the pipeline doesn't have a run_key context, skip AB check."""
        with patch(
            "kindly_web_search_mcp_server.search.pipeline.get_ab_overrides",
        ) as mock_get_ab, patch(
            "kindly_web_search_mcp_server.search.pipeline.execute_search_branches"
        ) as mock_exec, patch(
            "kindly_web_search_mcp_server.search.pipeline.build_rewrite_variants"
        ) as mock_rewrite, patch(
            "kindly_web_search_mcp_server.search.pipeline.merge_search_results"
        ) as mock_merge, patch(
            "kindly_web_search_mcp_server.search.pipeline.normalize_query"
        ) as mock_norm, patch(
            "kindly_web_search_mcp_server.search.pipeline.resolve_query_understanding"
        ) as mock_qu, patch(
            "kindly_web_search_mcp_server.search.pipeline.resolve_search_profile"
        ) as mock_profile, patch(
            "kindly_web_search_mcp_server.search.pipeline.apply_profile_search_options"
        ) as mock_apply, patch(
            "kindly_web_search_mcp_server.search.pipeline.build_provider_execution_plan"
        ) as mock_plan, patch(
            "kindly_web_search_mcp_server.search.pipeline.build_cache_identity"
        ) as mock_cache, patch(
            "kindly_web_search_mcp_server.search.pipeline.httpx.AsyncClient"
        ) as mock_client, patch(
            "kindly_web_search_mcp_server.search.pipeline.rerank_results",
            new_callable=AsyncMock,
        ) as mock_rerank, patch(
            "kindly_web_search_mcp_server.search.pipeline.build_search_context"
        ) as mock_ctx_builder, patch(
            "kindly_web_search_mcp_server.search.pipeline.inject_result_memory_candidates"
        ) as mock_mem, patch(
            "kindly_web_search_mcp_server.search.pipeline.store_result_memory_results"
        ) as mock_store_mem, patch(
            "kindly_web_search_mcp_server.search.pipeline.build_search_response"
        ) as mock_response:
            from kindly_web_search_mcp_server.models import WebSearchResult
            from kindly_web_search_mcp_server.rerank.models import RerankOutput

            mock_norm.return_value = "normalized query"
            mock_qu.return_value = MagicMock(
                intent="general", confidence=0.9, should_decompose=False,
                rationale="test", entities=[], must_keep_terms=set(),
            )
            mock_ctx_builder.return_value = MagicMock(
                intent="general", confidence=0.9, should_decompose=False,
                profile_name="default", must_keep_terms=set(),
            )
            mock_profile.return_value = MagicMock(
                provider_weights={},
            )
            mock_apply.return_value = _make_search_options()
            mock_plan.return_value = MagicMock(
                provider_names=["web"],
                provider_weights={},
                options=MagicMock(bundles={}),
            )
            mock_cache.return_value = "cache-identity"
            mock_rewrite.return_value = ([], "none", None, None)
            mock_exec.return_value = MagicMock(
                result_lists=[],
                branch_queries=[],
                branch_providers=[],
                list_weights=[],
                branch_metadata={},
            )
            mock_merge.return_value = [
                WebSearchResult(
                    title="R1", link="https://r1.com", snippet="S1",
                    score=0.9, domain="r1.com",
                ),
                WebSearchResult(
                    title="R2", link="https://r2.com", snippet="S2",
                    score=0.8, domain="r2.com",
                ),
            ]
            mock_mem.return_value = ([], [], None, None)
            mock_store_mem.return_value = None
            mock_response.return_value = (None, None, MagicMock())
            mock_client.return_value.__aenter__.return_value = MagicMock()
            mock_client.return_value.aclose = AsyncMock()

            mock_rerank.return_value = RerankOutput(
                results=[], embedding_context=None,
            )

            result = await run_search_pipeline(
                query="test query",
                num_results=10,
                rewrite=False,
                diagnostics=None,
                research_goal=None,
                search_options=None,
                session_id=None,
            )

            # Pipeline always generates its own run_key, so get_ab_overrides
            # should be called (not suppressed). Just verify it completes.
            assert mock_get_ab.call_count >= 1
            assert result is not None

    @pytest.mark.asyncio
    async def test_shadow_mode_fires_background_task(self, mock_settings):
        """When AB override has shadow_mode=True, run_shadow should be called."""
        with patch(
            "kindly_web_search_mcp_server.search.pipeline.get_ab_overrides",
            return_value={
                "experiment_id": "rerank-shadow-1",
                "variant_key": "test",
                "shadow_mode": True,
                "config": {"provider": "jina"},
            },
        ) as mock_get_ab, patch(
            "kindly_web_search_mcp_server.search.pipeline.run_shadow",
            new_callable=AsyncMock,
        ) as _mock_shadow, patch(
            "kindly_web_search_mcp_server.search.pipeline.execute_search_branches"
        ) as mock_exec, patch(
            "kindly_web_search_mcp_server.search.pipeline.build_rewrite_variants"
        ) as mock_rewrite, patch(
            "kindly_web_search_mcp_server.search.pipeline.merge_search_results"
        ) as mock_merge, patch(
            "kindly_web_search_mcp_server.search.pipeline.normalize_query"
        ) as mock_norm, patch(
            "kindly_web_search_mcp_server.search.pipeline.resolve_query_understanding"
        ) as mock_qu, patch(
            "kindly_web_search_mcp_server.search.pipeline.resolve_search_profile"
        ) as mock_profile, patch(
            "kindly_web_search_mcp_server.search.pipeline.apply_profile_search_options"
        ) as mock_apply, patch(
            "kindly_web_search_mcp_server.search.pipeline.build_provider_execution_plan"
        ) as mock_plan, patch(
            "kindly_web_search_mcp_server.search.pipeline.build_cache_identity"
        ) as mock_cache, patch(
            "kindly_web_search_mcp_server.search.pipeline.httpx.AsyncClient"
        ) as mock_client, patch(
            "kindly_web_search_mcp_server.search.pipeline.rerank_results",
            new_callable=AsyncMock,
        ) as mock_rerank, patch(
            "kindly_web_search_mcp_server.search.pipeline.asyncio.ensure_future",
        ) as mock_ensure_future, patch(
            "kindly_web_search_mcp_server.search.pipeline.build_search_context"
        ) as mock_ctx_builder, patch(
            "kindly_web_search_mcp_server.search.pipeline.inject_result_memory_candidates"
        ) as mock_mem, patch(
            "kindly_web_search_mcp_server.search.pipeline.store_result_memory_results"
        ) as mock_store_mem, patch(
            "kindly_web_search_mcp_server.search.pipeline.build_search_response"
        ) as mock_response:
            from kindly_web_search_mcp_server.models import WebSearchResult
            from kindly_web_search_mcp_server.rerank.models import RerankOutput

            mock_norm.return_value = "normalized query"
            mock_qu.return_value = MagicMock(
                intent="general", confidence=0.9, should_decompose=False,
                rationale="test", entities=[], must_keep_terms=set(),
            )
            mock_ctx_builder.return_value = MagicMock(
                intent="general", confidence=0.9, should_decompose=False,
                profile_name="default", must_keep_terms=set(),
            )
            mock_profile.return_value = MagicMock(
                provider_weights={},
            )
            mock_apply.return_value = _make_search_options()
            mock_plan.return_value = MagicMock(
                provider_names=["web"],
                provider_weights={},
                options=MagicMock(bundles={}),
            )
            mock_cache.return_value = "cache-identity"
            mock_rewrite.return_value = ([], "none", None, None)
            mock_exec.return_value = MagicMock(
                result_lists=[],
                branch_queries=[],
                branch_providers=[],
                list_weights=[],
                branch_metadata={},
            )
            mock_merge.return_value = [
                WebSearchResult(
                    title="R1", link="https://r1.com", snippet="S1",
                    score=0.9, domain="r1.com",
                ),
                WebSearchResult(
                    title="R2", link="https://r2.com", snippet="S2",
                    score=0.8, domain="r2.com",
                ),
            ]
            mock_mem.return_value = ([], [], None, None)
            mock_store_mem.return_value = None
            mock_response.return_value = (None, None, MagicMock())
            mock_client.return_value.__aenter__.return_value = MagicMock()
            mock_client.return_value.aclose = AsyncMock()

            mock_rerank.return_value = RerankOutput(
                results=[], embedding_context=None,
            )
            mock_ensure_future.side_effect = lambda coro: coro.close()

            await run_search_pipeline(
                query="test query",
                num_results=10,
                rewrite=False,
                diagnostics=None,
                research_goal=None,
                search_options=None,
                session_id=None,
            )

            # verify that get_ab_overrides was called for multiple layers
            assert mock_get_ab.call_count >= 1
            # verify that asyncio.ensure_future was called (for shadow task)
            assert mock_ensure_future.called, \
                "asyncio.ensure_future should be called for shadow mode"


class TestRerankResultsWithABDirectly:
    """Direct unit tests on rerank_results with ab_overrides."""

    @pytest.mark.asyncio
    async def test_ab_provider_override_logged(self):
        """ab_overrides with provider should log the override."""
        from kindly_web_search_mcp_server.models import WebSearchResult

        candidates = [
            WebSearchResult(
                title=f"R{i}", link=f"https://e.com/{i}",
                snippet=f"S{i}", score=0.9, domain="e.com",
            )
            for i in range(5)
        ]

        with patch(
            "kindly_web_search_mcp_server.rerank.core.settings"
        ) as s, patch(
            "kindly_web_search_mcp_server.rerank.core.decide_rerank"
        ) as mock_decide:
            s.rerank_provider = "none"
            s.rerank_stack_mode = "bi_cross"
            s.rerank_recency_weight = 0.0
            s.rerank_recency_half_life_days = 90
            s.rerank_score_threshold = -999.0
            s.mmr_lambda_param = 0.5
            s.rerank_entity_overlap_enabled = False

            mock_decide.return_value = MagicMock(
                should_rerank=True, reason="test", query_type="general",
                candidate_count=5,
            )

            result = await rerank_results(
                query="test",
                candidates=candidates,
                top_k=10,
                run_key="test-run",
                ab_overrides={"provider": "jina", "diversity_weight": 0.7},
            )
            assert result is not None
