"""Test OpenTelemetry telemetry setup for Grafana Cloud.

Two modes available:
1. OTLP Direct (no collector)
2. Prometheus + OTLP (with Alloy scraping)

USAGE:
    Mode 1 - OTLP Direct:
       $env:OTEL_EXPORTER_OTLP_ENDPOINT="https://otlp-gateway-prod-us-east-0.grafana.net/otlp"
       $env:OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic%20<YOUR_TOKEN>"
       .venv/Scripts/python.exe test_telemetry_setup.py

    Mode 2 - Prometheus + OTLP (recommended):
       $env:OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4318"  # Alloy collector
       $env:KINDLY_PROMETHEUS_PORT="9090"
       .venv/Scripts/python.exe test_telemetry_setup.py
       # Then configure Alloy to scrape localhost:9090/metrics
"""
import os
import sys
import time

sys.path.insert(0, 'src')
from dotenv import load_dotenv
load_dotenv('.env')

print("=" * 70)
print("GRAFANA CLOUD TELEMETRY TEST - COMPREHENSIVE")
print("=" * 70)

# Check configuration
endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
headers = os.environ.get("OTEL_EXPORTER_OTLP_HEADERS")
prometheus_port = os.environ.get("KINDLY_PROMETHEUS_PORT")

print(f"\nConfiguration:")
print(f"  OTLP Endpoint: {endpoint or 'NOT SET'}")
print(f"  Headers: {headers[:30] + '...' if headers else 'NOT SET'}")
print(f"  Prometheus Port: {prometheus_port or 'NOT SET (OTLP only)'}")

if not endpoint:
    print("\nERROR: OTEL_EXPORTER_OTLP_ENDPOINT not set!")
    print("Get endpoint from Grafana Cloud → Connections → OpenTelemetry")
    sys.exit(1)

# Determine mode
mode = "Prometheus + OTLP" if prometheus_port else "OTLP Direct"
print(f"\nMode: {mode}")

# Initialize telemetry
print("\nInitializing OpenTelemetry...")
from kindly_web_search_mcp_server.telemetry import (
    init_telemetry,
    get_tracer,
    get_meter,
    # Basic metrics
    record_provider_call,
    record_merge,
    record_search_request,
    record_cache_lookup,
    record_mcp_tool_call,
    record_content_resolution,
    # NEW: RRF merge metrics
    record_rrf_merge,
    record_rrf_score,
    # NEW: Query rewrite metrics
    record_query_rewrite,
    # NEW: Reranking metrics
    record_rerank_stage,
    record_diversity_removal,
    # NEW: Semantic cache metrics
    record_semantic_cache_lookup,
    # NEW: Circuit breaker metrics
    record_circuit_breaker_state,
    record_circuit_breaker_event,
    # NEW: Tool-specific metrics
    record_gemini_search,
    record_perplexity_search,
    record_youtube_transcript,
    record_youtube_search,
    record_tool_details,
    # Span helpers
    create_search_span,
    create_provider_span,
    create_merge_span,
    create_query_rewrite_span,
    create_rerank_span,
    create_circuit_breaker_span,
    create_cache_span,
    # Span enhancement
    add_results_to_span,
    add_query_rewrite_variants_to_span,
    add_rrf_merge_details_to_span,
    add_rerank_scores_to_span,
    set_span_success,
    set_span_error,
    # Constants
    SEARCH_QUERY,
    SEARCH_NUM_RESULTS_REQUESTED,
    SEARCH_NUM_RESULTS_RETURNED,
    SEARCH_PROVIDERS_REQUESTED,
    PROVIDER_NAME,
    HTTP_RESPONSE_STATUS_CODE,
    HTTP_REQUEST_METHOD,
    URL_FULL,
    SERVER_ADDRESS,
    RESULT_RRF_SCORE,
    RESULT_PROVIDER_SOURCES,
)
from opentelemetry import trace

init_telemetry(
    service_name="web-search-mcp-test",
    prometheus_port=int(prometheus_port) if prometheus_port else None,
)

tracer = get_tracer()
meter = get_meter()

print(f"Telemetry initialized: {mode}")

# === TEST 1: Complete Search Flow with RRF Details ===
print("\n[TEST 1] Simulating complete search flow with RRF details visible in Grafana...")

# Create a mock result object with RRF details
class MockResult:
    def __init__(self, title, link, snippet, score=None, providers=None):
        self.title = title
        self.link = link
        self.snippet = snippet
        self.score = score
        self.providers = providers

mock_results = [
    MockResult("Claude by Anthropic - AI Assistant", "https://claude.ai", "Claude is a next-generation AI assistant...", score=0.85, providers=["searxng", "gemini"]),
    MockResult("Claude AI - What is it?", "https://example.com/claude-ai", "Claude AI represents a breakthrough...", score=0.72, providers=["searxng"]),
    MockResult("Anthropic Claude Documentation", "https://docs.anthropic.com", "Official documentation for Claude...", score=0.65, providers=["gemini"]),
]

with create_search_span("Claude AI", 10, ["searxng", "gemini"]) as search_span:
    search_span.set_attribute("test.mode", mode)

    # Simulate provider calls with RRF scores
    with create_provider_span("searxng", "Claude AI", 10, "http://localhost:8080/search?q=Claude+AI") as provider_span:
        time.sleep(0.05)  # Simulate latency
        provider_span.set_attribute(HTTP_RESPONSE_STATUS_CODE, 200)
        provider_span.set_attribute(PROVIDER_NAME, "searxng")
        provider_span.set_attribute("provider.result_count", 2)
        add_results_to_span(provider_span, mock_results[:2], include_rrf_details=True)
        set_span_success(provider_span, 2)

    with create_provider_span("gemini", "Claude AI", 10, "https://generativelanguage.googleapis.com/v1/models") as gemini_span:
        time.sleep(0.03)
        gemini_span.set_attribute(HTTP_RESPONSE_STATUS_CODE, 200)
        gemini_span.set_attribute(PROVIDER_NAME, "gemini")
        gemini_span.set_attribute("provider.result_count", 1)
        add_results_to_span(gemini_span, mock_results[2:], include_rrf_details=True)
        set_span_success(gemini_span, 1)

    # NEW: Simulate RRF merge with details
    with create_merge_span(2, 10) as merge_span:
        merge_span.set_attribute("merge.output_count", 3)
        merge_span.set_attribute("merge.duration_ms", 1.5)
        # Add RRF merge details
        add_rrf_merge_details_to_span(
            merge_span,
            provider_counts={"searxng": 2, "gemini": 1},
            discarded_urls=["https://duplicate-url.com"],
            overlapping_urls=["https://claude.ai"],  # Found by both providers
        )
        set_span_success(merge_span, 3)

    # NEW: Simulate query rewrite
    with create_query_rewrite_span("Claude AI", "expand") as rewrite_span:
        rewrite_span.set_attribute("rewrite.variant_count", 2)
        # Add variants as events
        class MockVariant:
            def __init__(self, type, text):
                self.type = type
                self.text = text
        add_query_rewrite_variants_to_span(rewrite_span, [
            MockVariant("original", "Claude AI"),
            MockVariant("official_docs", "Claude AI official documentation"),
        ])

    # NEW: Simulate reranking
    with create_rerank_span("jina", 10) as rerank_span:
        rerank_span.set_attribute("rerank.output_count", 3)
        add_rerank_scores_to_span(rerank_span, [0.95, 0.85, 0.65], "jina")

    with create_rerank_span("diversity", 3) as diversity_span:
        diversity_span.set_attribute("rerank.removed_count", 1)

    # Final search results with RRF details
    add_results_to_span(search_span, mock_results, include_rrf_details=True)
    set_span_success(search_span, 3)

print("  ✓ Search flow trace sent with RRF scores, providers, merge details")

# === TEST 2: Provider Metrics ===
print("\n[TEST 2] Recording provider metrics...")

record_provider_call(
    provider="searxng",
    duration_seconds=0.05,
    result_count=10,
    status_code=200,
)

record_provider_call(
    provider="gemini",
    duration_seconds=0.02,
    result_count=5,
    status_code=200,
)

record_provider_call(
    provider="tavily",
    duration_seconds=0.5,
    result_count=0,
    status_code=500,
    error_type="HTTP_500",
)

print("  ✓ Provider metrics recorded")

# === TEST 3: NEW RRF Merge Metrics ===
print("\n[TEST 3] Recording RRF merge metrics...")

record_rrf_merge(
    input_lists=2,
    input_total=20,
    output_total=10,
    discarded_count=5,
    overlap_rate=0.25,
    provider_contributions={"searxng": 6, "gemini": 4},
)

record_rrf_score(score=0.85, position=1)
record_rrf_score(score=0.72, position=2)

print("  ✓ RRF merge metrics recorded (discarded, overlap, provider contribution, scores)")

# === TEST 4: NEW Query Rewrite Metrics ===
print("\n[TEST 4] Recording query rewrite metrics...")

record_query_rewrite(
    policy="bypass",
    variant_count=1,
    has_precision_signals=True,
    duration_seconds=0.001,
)

record_query_rewrite(
    policy="expand",
    variant_count=3,
    has_precision_signals=False,
    duration_seconds=0.15,
    model="mistral-small-2603",
)

print("  ✓ Query rewrite metrics recorded (policy, variants, duration)")

# === TEST 5: NEW Reranking Metrics ===
print("\n[TEST 5] Recording reranking metrics...")

record_rerank_stage(
    stage="bi_encoder",
    input_count=100,
    output_count=20,
    duration_seconds=0.05,
)

record_rerank_stage(
    stage="jina",
    input_count=20,
    output_count=10,
    duration_seconds=0.10,
    relevance_scores=[0.95, 0.88, 0.82, 0.75, 0.70, 0.65, 0.60, 0.55, 0.50, 0.45],
)

record_rerank_stage(
    stage="diversity",
    input_count=10,
    output_count=8,
    duration_seconds=0.001,
)

record_diversity_removal(similarity_score=0.92, threshold=0.85)
record_diversity_removal(similarity_score=0.88, threshold=0.85)

print("  ✓ Reranking metrics recorded (stages, scores, diversity removals)")

# === TEST 6: Cache Metrics ===
print("\n[TEST 6] Recording cache metrics...")

record_cache_lookup("exact", True, 0.001)
record_cache_lookup("exact", False, 0.001)

# NEW: Semantic cache with similarity score
record_semantic_cache_lookup(
    similarity_score=0.88,
    hit=True,
    content_type="technical",
    search_type="hybrid_rrf",
    vector_distance=0.12,
)

record_semantic_cache_lookup(
    similarity_score=0.75,
    hit=False,
    content_type="general",
)

record_semantic_cache_lookup(
    similarity_score=0.92,
    hit=True,
    content_type="faq",
    ttl_seconds=604800,  # 7 days
)

print("  ✓ Cache metrics recorded (exact + semantic with similarity scores)")

# === TEST 7: NEW Circuit Breaker Metrics ===
print("\n[TEST 7] Recording circuit breaker metrics...")

record_circuit_breaker_state(provider="tavily", state="open", failure_count=3)
record_circuit_breaker_state(provider="searxng", state="closed", failure_count=0)

record_circuit_breaker_event(provider="tavily", event="trip", failure_threshold=3)
record_circuit_breaker_event(provider="brave", event="reset")

print("  ✓ Circuit breaker metrics recorded (state, events)")

# === TEST 8: Search Metrics ===
print("\n[TEST 8] Recording search metrics...")

record_search_request(
    providers_used=["searxng", "gemini"],
    duration_seconds=0.15,
    result_count=10,
)

record_merge(
    duration_seconds=0.002,
    input_lists=2,
    output_count=10,
)

print("  ✓ Search metrics recorded")

# === TEST 9: MCP Tool Metrics ===
print("\n[TEST 9] Recording MCP tool metrics...")

record_mcp_tool_call("web_search", True)
record_mcp_tool_call("get_content", True)
record_mcp_tool_call("gemini_search", False)

# NEW: Detailed tool metrics
record_tool_details(
    tool_name="web_search",
    input_query_length=15,
    output_result_count=10,
)

record_tool_details(
    tool_name="batch_get_content",
    input_url_count=5,
)

record_tool_details(
    tool_name="get_content",
    output_content_length=5000,
)

print("  ✓ MCP tool metrics recorded (basic + detailed)")

# === TEST 10: NEW Gemini/Perplexity Metrics ===
print("\n[TEST 10] Recording Gemini and Perplexity search metrics...")

record_gemini_search(
    grounding_queries=3,
    grounding_chunks=5,
    structured_output=False,
)

record_gemini_search(
    grounding_queries=2,
    grounding_chunks=8,
    structured_output=True,
)

record_perplexity_search(
    depth="normal",
    source_count=5,
    model="sonar",
)

record_perplexity_search(
    depth="deep",
    source_count=10,
    model="sonar-reasoning",
)

print("  ✓ Gemini and Perplexity metrics recorded")

# === TEST 11: NEW YouTube Metrics ===
print("\n[TEST 11] Recording YouTube metrics...")

record_youtube_transcript(
    format="text",
    language="en",
    is_translated=False,
    duration_seconds=300,
)

record_youtube_transcript(
    format="timestamped",
    language="es",
    is_translated=True,
    duration_seconds=450,
)

record_youtube_search(num_results=5)

print("  ✓ YouTube metrics recorded")

# === TEST 12: Content Resolution Metrics ===
print("\n[TEST 12] Recording content resolution metrics...")

record_content_resolution("stackexchange", "https://stackoverflow.com/questions/123", True, 5000, 0.1, extraction_method="api")
record_content_resolution("github_issue", "https://github.com/anthropics/anthropic-sdk-python/issues/456", True, 2000, 0.2, extraction_method="graphql")
record_content_resolution("wikipedia", "https://en.wikipedia.org/wiki/Claude_AI", True, 15000, 0.05, extraction_method="api")
record_content_resolution("http_extract", "https://example.com/article", False, None, 0.5, extraction_method="trafilatura")

print("  ✓ Content resolution metrics recorded")

# === TEST 13: Error Handling ===
print("\n[TEST 13] Testing error handling...")

try:
    raise ValueError("Simulated error for testing")
except Exception as e:
    with tracer.start_as_current_span("error_test") as error_span:
        set_span_error(error_span, e, "test_error")
        print("  ✓ Error span with exception recorded")

print("\nAll tests completed successfully!")

if prometheus_port:
    print(f"\nPrometheus metrics available at: http://localhost:{prometheus_port}/metrics")
    print("Configure Alloy to scrape this endpoint")
    print("\nWaiting 5 seconds for metrics to be available...")
    time.sleep(5)

    # Try to fetch metrics
    import urllib.request
    try:
        url = f"http://localhost:{prometheus_port}/metrics"
        with urllib.request.urlopen(url) as response:
            content = response.read().decode('utf-8')
            lines = content.split('\n')
            # Look for all new metrics
            metric_patterns = [
                'web_search_', 'mcp_', 'rrf_', 'rewrite_', 'rerank_',
                'circuit_', 'gemini_', 'perplexity_', 'youtube_'
            ]
            metric_lines = [l for l in lines if any(l.startswith(p) for p in metric_patterns)]
            print(f"\nMetrics found ({len(metric_lines)} lines):")
            for line in metric_lines[:20]:
                print(f"  {line}")
    except Exception as e:
        print(f"Could not fetch metrics: {e}")
else:
    print("\nWaiting 5 seconds for batch export...")
    time.sleep(5)

print("\n" + "=" * 70)
print("TELEMETRY VERIFICATION COMPLETE")
print("=" * 70)

print("\nCheck Grafana Cloud within 1-2 minutes:")
print("  1. Navigate to Explore → Traces")
print("  2. Search for service: 'web-search-mcp-test'")
print("  3. Look for trace named 'web_search'")
print("  4. Expand span events to see:")
print("     - Result titles, URLs, RRF scores, providers")
print("     - Query rewrite variants")
print("     - RRF merge details (discarded, overlap, provider contribution)")
print("     - Rerank scores")
print("  5. Check metrics:")
print("     - web_search_provider_calls_total")
print("     - web_search_rrf_merge_total (NEW)")
print("     - web_search_rrf_provider_contribution (NEW)")
print("     - web_search_rrf_score_distribution (NEW)")
print("     - web_search_query_rewrite_total (NEW)")
print("     - web_search_rerank_total (NEW)")
print("     - web_search_rerank_scores (NEW)")
print("     - web_search_provider_circuit_state (NEW)")
print("     - mcp_gemini_search_details (NEW)")
print("     - mcp_perplexity_search_details (NEW)")
print("     - mcp_youtube_transcript_details (NEW)")
print("=" * 70)