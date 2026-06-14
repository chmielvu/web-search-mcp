# AGENTS.md - Search Pipeline Core

This directory contains the core search pipeline for the Kindly Web Search MCP Server.

## Structure

search/
|-- pipeline.py              # Main orchestration: understanding -> rewrite -> search -> merge -> rerank
|-- merge.py                 # RRF (k=60) merge across providers
|-- provider_plan.py         # Profile-derived provider weights & allow-lists
|-- provider_options.py      # Provider-specific argument construction
|-- provider_call.py         # Individual provider execution
|-- provider_config.py       # Provider registry & configuration
|-- query_policy.py          # Lightweight rewrite policy model
|-- query_execution.py       # Query execution coordination
|-- profiles/                # Search profiles (intent-specific configs)
-- understanding/           # Query understanding & intent resolution
    -- resolver.py          # LLM-backed query understanding

## Key Patterns

### Adding a New Search Provider
1. Create module in search/ with search_provider(query, num_results, http_client, diagnostics) returning normalized results
2. Register in search/__init__.py and search/provider_config.py
3. Add profile hooks in search/provider_plan.py if provider needs intent-specific weights/arguments
4. Add env var config in settings.py if needed

### Provider Weights & Profiles
- Provider weights are derived from SearchProfile (see provider_plan.py)
- Profiles map intents to provider configurations
- RRF merge uses k=60 by default

### Query Understanding
- understanding/resolver.py handles LLM-backed intent resolution
- query_policy.py provides lightweight rewrite policy for live pipeline

## Testing
pytest tests/test_search_orchestrator.py tests/test_search_router.py tests/test_provider_plan.py tests/test_provider_config.py -v

## Conventions
- All provider implementations return normalized SearchResult objects
- Diagnostics are collected per-provider for observability
- Provider errors are caught and logged, not propagated (graceful degradation)
