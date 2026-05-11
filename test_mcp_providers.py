"""Test MCP web_search with detailed provider inspection."""
import logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
import asyncio
import sys
import os
sys.path.insert(0, 'src')
from dotenv import load_dotenv
load_dotenv('.env')

print('=== PROVIDER MODE CONFIGURATION ===')
print(f'KINDLY_GEMINI_SEARCH_MODE: {os.environ.get("KINDLY_GEMINI_SEARCH_MODE", "NOT SET")}')
print(f'KINDLY_TAVILY_MODE: {os.environ.get("KINDLY_TAVILY_MODE", "NOT SET")}')
print(f'KINDLY_BRAVE_MODE: {os.environ.get("KINDLY_BRAVE_MODE", "NOT SET")}')
print(f'KINDLY_JINA_MODE: {os.environ.get("KINDLY_JINA_MODE", "NOT SET")}')
print(f'SEARXNG_BASE_URL: {os.environ.get("SEARXNG_BASE_URL", "NOT SET")}')
print(f'KINDLY_GEMINI_GROUNDING_MODEL: {os.environ.get("KINDLY_GEMINI_GROUNDING_MODEL", "NOT SET")}')

from kindly_web_search_mcp_server.search import (
    search_searxng,
    search_gemini,
    ProviderConfig,
    ProviderMode,
)
from kindly_web_search_mcp_server.search.provider_config import get_provider_configs
from kindly_web_search_mcp_server.search import search_single_query
from kindly_web_search_mcp_server.settings import settings

async def test_searxng_direct():
    """Test SearXNG provider directly."""
    print('\n=== TESTING SEARXNG DIRECTLY ===')
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30) as client:
            results = await search_searxng(
                query="Claude AI",
                num_results=5,
                http_client=client,
            )
        print(f'SearXNG Results: {len(results)}')
        for i, r in enumerate(results[:5]):
            print(f'  [{i}] {r.providers} | {r.title[:50]}... -> {r.link}')
        return len(results) > 0
    except Exception as e:
        print(f'Error: {type(e).__name__}: {e}')
        return False


async def test_gemini_provider_direct():
    """Test Gemini provider grounding directly."""
    print('\n=== TESTING GEMINI PROVIDER DIRECTLY ===')
    print(f'Using model: {settings.gemini_grounding_model}')
    try:
        results = await search_gemini(
            query="Claude AI",
            num_results=5,
        )
        print(f'Gemini Provider Results: {len(results)}')
        for i, r in enumerate(results[:5]):
            print(f'  [{i}] {r.providers} | {r.title[:50]}... -> {r.link}')
            print(f'      Snippet: {r.snippet[:100]}...')
        return len(results) > 0
    except Exception as e:
        print(f'Error: {type(e).__name__}: {e}')
        import traceback
        traceback.print_exc()
        return False


def test_provider_configs():
    """Check provider configuration."""
    print('\n=== PROVIDER CONFIG REGISTRY ===')
    configs = get_provider_configs()
    for name, config in configs.items():
        print(f'{name}:')
        print(f'  mode: {config.mode}')
        print(f'  available: {config.is_available()}')
        print(f'  should_fire (no providers param): {config.should_fire()}')


async def test_full_search():
    """Test full search orchestration."""
    print('\n=== TESTING FULL SEARCH (search_single_query) ===')
    try:
        results = await search_single_query(
            query="Claude AI",
            num_results=10,
        )
        print(f'Total results: {len(results)}')

        # Group by provider
        provider_counts = {}
        for r in results:
            for p in (r.providers or []):
                provider_counts[p] = provider_counts.get(p, 0) + 1

        print(f'Results by provider: {provider_counts}')

        for i, r in enumerate(results[:10]):
            print(f'  [{i}] {r.providers} | {r.title[:40]}... -> {r.link[:60]}...')
        return results
    except Exception as e:
        print(f'Error: {type(e).__name__}: {e}')
        import traceback
        traceback.print_exc()
        return None


async def main():
    # Check provider config registry
    test_provider_configs()

    # Test SearXNG directly
    await test_searxng_direct()

    # Test Gemini provider directly
    await test_gemini_provider_direct()

    # Test full search
    await test_full_search()

asyncio.run(main())