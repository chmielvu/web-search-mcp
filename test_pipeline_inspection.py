"""COMPREHENSIVE PIPELINE INSPECTION TEST

Tests every step of the web_search pipeline with detailed metrics:
1. Provider discovery and configuration
2. Individual provider calls (timing, raw response, parsing)
3. RRF merge algorithm (input lists, weights, scoring)
4. Result deduplication and diversity filtering
5. Final response construction
"""
import logging
import time
import json
import sys
import os
import asyncio
from dataclasses import dataclass, field
from typing import Any

sys.path.insert(0, 'src')
from dotenv import load_dotenv
load_dotenv('.env')

# Detailed logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s.%(msecs)03d | %(name)s | %(levelname)s | %(message)s',
    datefmt='%H:%M:%S'
)

from kindly_web_search_mcp_server.search import search_single_query
from kindly_web_search_mcp_server.search.provider_config import (
    get_provider_configs,
    resolve_providers_for_search,
    ProviderConfig,
)
from kindly_web_search_mcp_server.search.merge import merge_search_results
from kindly_web_search_mcp_server.search.normalize import canonicalize_url
from kindly_web_search_mcp_server.settings import settings
import httpx


@dataclass
class ProviderMetrics:
    """Detailed metrics for a single provider call."""
    name: str
    mode: str
    available: bool
    should_fire: bool
    call_started: float = 0.0
    call_completed: float = 0.0
    duration_ms: float = 0.0
    http_status: int = 0
    error_type: str = ""
    error_message: str = ""
    raw_result_count: int = 0
    parsed_result_count: int = 0
    result_titles: list[str] = field(default_factory=list)
    result_urls: list[str] = field(default_factory=list)
    result_snippets_length: list[int] = field(default_factory=list)
    result_providers_tagged: list[list[str]] = field(default_factory=list)


@dataclass
class MergeMetrics:
    """Detailed metrics for RRF merge."""
    input_lists_count: int = 0
    total_input_results: int = 0
    unique_urls_before_merge: int = 0
    rrf_k_param: int = 0
    provider_weights: dict[str, float] = field(default_factory=dict)
    score_calculation_examples: list[dict] = field(default_factory=list)
    unique_urls_after_merge: int = 0
    final_result_count: int = 0
    dedup_collisions: int = 0
    duration_ms: float = 0.0


@dataclass
class PipelineMetrics:
    """Complete pipeline execution metrics."""
    query: str
    num_results_requested: int
    total_duration_ms: float = 0.0
    providers: list[ProviderMetrics] = field(default_factory=list)
    merge: MergeMetrics = field(default_factory=MergeMetrics)
    final_results_count: int = 0
    final_providers_used: list[str] = field(default_factory=list)


def inspect_provider_configs() -> dict:
    """STEP 1: Inspect provider discovery and configuration."""
    print("\n" + "="*80)
    print("STEP 1: PROVIDER CONFIGURATION INSPECTION")
    print("="*80)

    configs = get_provider_configs()
    print(f"\nRegistry contains {len(configs)} providers:")

    config_data = {}
    for name, config in configs.items():
        print(f"\n  {name.upper()}:")
        print(f"    mode: {config.mode.value}")
        print(f"    env_key: {config.env_key}")
        print(f"    is_free: {config.is_free}")
        print(f"    requires_key: {config.requires_key}")
        print(f"    available: {config.is_available()}")
        print(f"    should_fire(): {config.should_fire()}")

        # Check actual env var
        env_val = os.environ.get(config.env_key, "NOT SET") if config.env_key else "N/A"
        print(f"    env_var_value: {env_val[:50] if len(env_val) > 50 else env_val}...")

        config_data[name] = {
            "mode": config.mode.value,
            "available": config.is_available(),
            "should_fire": config.should_fire(),
        }

    # Resolve for search
    active = resolve_providers_for_search(None)
    print(f"\n  ACTIVE PROVIDERS (resolve_providers_for_search): {[c.name for c in active]}")

    return config_data


async def test_provider_direct(
    config: ProviderConfig,
    query: str,
    num_results: int,
    http_client: httpx.AsyncClient,
) -> tuple[ProviderMetrics, list[Any]]:
    """STEP 2: Test individual provider with full inspection."""
    metrics = ProviderMetrics(
        name=config.name,
        mode=config.mode.value,
        available=config.is_available(),
        should_fire=config.should_fire(),
    )
    results = []

    print(f"\n  {'='*60}")
    print(f"  PROVIDER: {config.name.upper()}")
    print(f"  {'='*60}")

    metrics.call_started = time.time()

    try:
        print(f"  Calling {config.name}.search_fn(query='{query}', num_results={num_results})")

        # Call provider
        results = await config.search_fn(query, num_results=num_results, http_client=http_client)

        metrics.call_completed = time.time()
        metrics.duration_ms = (metrics.call_completed - metrics.call_started) * 1000
        metrics.http_status = 200  # Success
        metrics.parsed_result_count = len(results)
        metrics.raw_result_count = len(results)

        print(f"  Duration: {metrics.duration_ms:.1f}ms")
        print(f"  HTTP Status: 200 OK")
        print(f"  Results returned: {len(results)}")

        # Inspect each result
        for i, r in enumerate(results):
            print(f"\n    Result[{i}]:")
            print(f"      title: {r.title[:60]}...")
            print(f"      link: {r.link}")
            print(f"      snippet length: {len(r.snippet)} chars")
            print(f"      providers tag (before merge): {r.providers}")

            metrics.result_titles.append(r.title[:60])
            metrics.result_urls.append(r.link)
            metrics.result_snippets_length.append(len(r.snippet))
            metrics.result_providers_tagged.append(r.providers or [])

    except Exception as e:
        metrics.call_completed = time.time()
        metrics.duration_ms = (metrics.call_completed - metrics.call_started) * 1000
        metrics.error_type = type(e).__name__
        metrics.error_message = str(e)[:300]

        # Extract HTTP status from error if available
        if "500" in str(e):
            metrics.http_status = 500
        elif "503" in str(e):
            metrics.http_status = 503
        elif "429" in str(e):
            metrics.http_status = 429
        elif "404" in str(e):
            metrics.http_status = 404

        print(f"  ERROR: {metrics.error_type}")
        print(f"  Message: {metrics.error_message}")
        print(f"  Duration: {metrics.duration_ms:.1f}ms")
        print(f"  HTTP Status inferred: {metrics.http_status}")

    return metrics, results


async def test_rrf_merge_detailed(
    all_results: list[list[Any]],
    provider_names: list[str],
) -> MergeMetrics:
    """STEP 3: Inspect RRF merge algorithm in detail with score calculations."""
    print("\n" + "="*80)
    print("STEP 3: RRF MERGE ALGORITHM INSPECTION")
    print("="*80)

    metrics = MergeMetrics()

    start_time = time.time()

    # Count input
    metrics.input_lists_count = len(all_results)
    metrics.total_input_results = sum(len(r) for r in all_results)

    print(f"\n  INPUT ANALYSIS:")
    print(f"    Number of input lists: {metrics.input_lists_count}")
    print(f"    Total input results: {metrics.total_input_results}")

    for i, results in enumerate(all_results):
        print(f"    List[{i}] ({provider_names[i] if i < len(provider_names) else 'unknown'}): {len(results)} results")

    # Count unique URLs before merge using canonicalize_url
    all_urls = set()
    url_source_map = {}  # Track which URLs came from which providers
    for list_idx, results in enumerate(all_results):
        for r in results:
            canonical = canonicalize_url(r.link)
            all_urls.add(canonical)
            if canonical not in url_source_map:
                url_source_map[canonical] = []
            url_source_map[canonical].append({
                "provider": provider_names[list_idx] if list_idx < len(provider_names) else "unknown",
                "rank": len(url_source_map[canonical]) + 1,  # Rank in this list
                "original_url": r.link,
                "title": r.title[:50],
            })

    metrics.unique_urls_before_merge = len(all_urls)
    print(f"    Unique URLs in input: {metrics.unique_urls_before_merge}")

    # RRF parameters from settings
    metrics.rrf_k_param = settings.rrf_k
    metrics.provider_weights = settings.rrf_provider_weights
    print(f"\n  RRF PARAMETERS FROM settings.py:")
    print(f"    k (damping): {metrics.rrf_k_param}")
    print(f"    provider_weights: {json.dumps(metrics.provider_weights, indent=4)}")

    # MANUALLY CALCULATE RRF SCORES for demonstration
    print(f"\n  RRF SCORE CALCULATION DEMO (Formula: score += w_provider × 1/(k + rank)):")
    print(f"    k={metrics.rrf_k_param}, so 1/(k+1)=1/{metrics.rrf_k_param+1}={1/(metrics.rrf_k_param+1):.5f}")

    manual_scores = {}
    for canonical, sources in url_source_map.items():
        total_score = 0.0
        score_breakdown = []
        for src in sources:
            weight = metrics.provider_weights.get(src["provider"], 1.0)
            rank = src["rank"]
            contribution = weight * (1.0 / (metrics.rrf_k_param + rank))
            total_score += contribution
            score_breakdown.append({
                "provider": src["provider"],
                "weight": weight,
                "rank": rank,
                "contribution": f"{contribution:.5f}",
            })
        manual_scores[canonical] = {
            "total_score": total_score,
            "breakdown": score_breakdown,
            "title": sources[0]["title"],
        }

    # Show top 10 score calculations
    sorted_scores = sorted(manual_scores.items(), key=lambda x: -x[1]["total_score"])
    print(f"\n  TOP 10 RRF SCORE CALCULATIONS:")
    for i, (url, data) in enumerate(sorted_scores[:10]):
        print(f"\n    [{i}] URL: {url[:60]}...")
        print(f"        Title: {data['title']}...")
        print(f"        Total Score: {data['total_score']:.5f}")
        print(f"        Score breakdown:")
        for bd in data["breakdown"]:
            print(f"          {bd['provider']}: w={bd['weight']}, rank={bd['rank']} → +{bd['contribution']}")

        metrics.score_calculation_examples.append({
            "url": url,
            "total_score": data["total_score"],
            "breakdown": data["breakdown"],
        })

    # Perform actual merge
    merged = merge_search_results(all_results)

    metrics.duration_ms = (time.time() - start_time) * 1000
    metrics.final_result_count = len(merged)
    metrics.unique_urls_after_merge = len(set(canonicalize_url(r.link) for r in merged))
    metrics.dedup_collisions = metrics.unique_urls_before_merge - metrics.unique_urls_after_merge

    print(f"\n  MERGE OUTPUT:")
    print(f"    Duration: {metrics.duration_ms:.1f}ms")
    print(f"    Final results: {metrics.final_result_count}")
    print(f"    Unique URLs after: {metrics.unique_urls_after_merge}")
    print(f"    Dedup collisions resolved: {metrics.dedup_collisions}")

    print(f"\n  ACTUAL MERGED RESULTS (from merge_search_results):")
    for i, r in enumerate(merged[:10]):
        score = getattr(r, 'score', None)
        score_str = f"{score:.5f}" if score else "N/A"
        print(f"    [{i}] score={score_str} | providers={r.providers} | {r.title[:40]}...")
        print(f"        URL: {r.link}")

    return metrics


async def test_final_response(query: str, num_results: int) -> tuple[PipelineMetrics, list[Any]]:
    """STEP 4: Test full pipeline and final response construction."""
    print("\n" + "="*80)
    print("STEP 4: FINAL RESPONSE VIA search_single_query")
    print("="*80)

    pipeline_start = time.time()

    metrics = PipelineMetrics(
        query=query,
        num_results_requested=num_results,
    )

    # Call the full pipeline
    final_results = await search_single_query(query, num_results=num_results)

    metrics.total_duration_ms = (time.time() - pipeline_start) * 1000
    metrics.final_results_count = len(final_results)

    # Analyze final results
    provider_counts = {}
    for r in final_results:
        for p in (r.providers or []):
            provider_counts[p] = provider_counts.get(p, 0) + 1

    metrics.final_providers_used = list(provider_counts.keys())

    print(f"\n  Full search_single_query duration: {metrics.total_duration_ms:.1f}ms")
    print(f"  Final results count: {len(final_results)}")
    print(f"  Results by provider: {provider_counts}")

    # Check if providers field is populated correctly
    print(f"\n  PROVIDER TAGGING ANALYSIS:")
    providers_populated = sum(1 for r in final_results if r.providers and len(r.providers) > 0)
    providers_empty = sum(1 for r in final_results if not r.providers or len(r.providers) == 0)
    print(f"    Results with providers tag: {providers_populated}")
    print(f"    Results with empty providers: {providers_empty}")

    print(f"\n  ALL FINAL RESULTS:")
    for i, r in enumerate(final_results):
        score = getattr(r, 'score', None)
        score_str = f"{score:.5f}" if score else "N/A"
        print(f"    [{i}] score={score_str} | providers={r.providers} | {r.title[:50]}...")

    return metrics, final_results


async def test_full_pipeline(query: str = "Claude AI", num_results: int = 10):
    """Complete pipeline test with detailed inspection."""
    print("\n" + "="*80)
    print("COMPREHENSIVE PIPELINE EXECUTION TEST")
    print("="*80)
    print(f"Query: '{query}'")
    print(f"Requested results: {num_results}")
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    # STEP 1: Provider configs
    config_data = inspect_provider_configs()

    # STEP 2: Individual provider calls
    print("\n" + "="*80)
    print("STEP 2: INDIVIDUAL PROVIDER CALLS")
    print("="*80)

    all_results: list[list[Any]] = []
    provider_names: list[str] = []
    provider_metrics_list: list[ProviderMetrics] = []

    async with httpx.AsyncClient(timeout=30) as client:
        # Test each active provider
        active_configs = resolve_providers_for_search(None)

        for config in active_configs:
            metrics, results = await test_provider_direct(
                config,
                query,
                num_results,
                client,
            )
            provider_metrics_list.append(metrics)

            # Collect successful results for merge test
            if len(results) > 0:
                all_results.append(results)
                provider_names.append(config.name)

    # STEP 3: RRF Merge (if we have results)
    if len(all_results) > 0:
        merge_metrics = await test_rrf_merge_detailed(all_results, provider_names)
    else:
        merge_metrics = MergeMetrics()
        print("\n  No results to merge - all providers failed")

    # STEP 4: Final response
    final_metrics, final_results = await test_final_response(query, num_results)

    # STEP 5: Summary
    print("\n" + "="*80)
    print("PIPELINE SUMMARY")
    print("="*80)
    print(f"\n  PROVIDER CALL SUMMARY:")
    for pm in provider_metrics_list:
        status = "✅" if pm.parsed_result_count > 0 else "❌"
        print(f"    {status} {pm.name}: {pm.duration_ms:.1f}ms, {pm.parsed_result_count} results")
        if pm.error_type:
            print(f"       Error: {pm.error_type} (HTTP {pm.http_status})")

    print(f"\n  MERGE SUMMARY:")
    if merge_metrics.input_lists_count > 0:
        print(f"    Input: {merge_metrics.total_input_results} results from {merge_metrics.input_lists_count} providers")
        print(f"    Output: {merge_metrics.final_result_count} unique results")
        print(f"    Dedup: {merge_metrics.dedup_collisions} collisions resolved")
    else:
        print(f"    No input to merge")

    print(f"\n  FINAL OUTPUT:")
    print(f"    Results: {final_metrics.final_results_count}")
    print(f"    Providers used: {final_metrics.final_providers_used}")
    print(f"    Total pipeline duration: {final_metrics.total_duration_ms:.1f}ms")

    return {
        "provider_configs": config_data,
        "provider_metrics": [pm for pm in provider_metrics_list],
        "merge_metrics": merge_metrics,
        "final_metrics": final_metrics,
    }


# Run the test
asyncio.run(test_full_pipeline())