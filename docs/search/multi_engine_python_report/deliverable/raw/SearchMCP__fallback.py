from web_mcp.config import settings
from web_mcp.search.base import SearchProvider, SearchResponse
from web_mcp.search.google import GOOGLE_TIMEOUT, GoogleProvider
from web_mcp.search.relevance import (
    QUERY_INTENT_SECURITY,
    detect_query_intent,
    is_low_quality,
    merge_ranked_results,
    rank_search_results,
)
from web_mcp.search.searxng import SearxNGProvider
from web_mcp.utils.logger import get_logger
from web_mcp.utils.rate_limiter import RateLimiter


class FallbackSearchProvider(SearchProvider):
    def __init__(
        self,
        searxng_url: str | None = None,
        fallback_enabled: bool | None = None,
        rate_limiter: RateLimiter | None = None,
        min_quality_score: float | None = None,
    ):
        self._searxng = SearxNGProvider(base_url=searxng_url)
        self._google = GoogleProvider(timeout=GOOGLE_TIMEOUT)
        self._fallback_enabled = (
            fallback_enabled if fallback_enabled is not None else settings.FALLBACK_ENABLED
        )
        self._rate_limiter = rate_limiter or RateLimiter()
        self._logger = get_logger("web_mcp")
        self._min_quality_score = (
            min_quality_score
            if min_quality_score is not None
            else settings.SEARCH_MIN_QUALITY_SCORE
        )

    @property
    def name(self) -> str:
        return "fallback"

    @property
    def is_available(self) -> bool:
        return self._searxng.is_available or (self._fallback_enabled and self._google.is_available)

    async def search(self, query: str, category: str = "general", limit: int = 5) -> SearchResponse:
        await self._rate_limiter.acquire()

        if not self._searxng.is_available and not self._fallback_enabled:
            self._logger.warning("No search providers available")
            return SearchResponse(
                results=[],
                suggestions=[],
                provider=self.name,
                query=query,
            )

        searxng_response = await self._searxng.search(query, category, limit)
        searxng_ranked = rank_search_results(query, searxng_response.results, limit)
        searxng_results = searxng_ranked.results

        intent = detect_query_intent(query)
        should_use_quality_gate = category == "general" and intent == QUERY_INTENT_SECURITY
        quality_triggered = should_use_quality_gate and is_low_quality(
            searxng_ranked,
            self._min_quality_score,
        )

        if searxng_results and not quality_triggered:
            self._logger.info(
                f"SearxNG returned {len(searxng_results)} ranked results",
                extra={
                    "query": query,
                    "provider": "searxng",
                    "quality_score": searxng_ranked.quality_score,
                },
            )
            return SearchResponse(
                results=searxng_results,
                suggestions=searxng_response.suggestions,
                provider=searxng_response.provider,
                query=query,
            )

        if not self._fallback_enabled:
            message = "SearxNG returned no results, fallback disabled"
            if quality_triggered:
                message = "SearxNG quality below threshold, fallback disabled"
            self._logger.info(
                message,
                extra={
                    "query": query,
                    "quality_score": searxng_ranked.quality_score,
                },
            )
            return SearchResponse(
                results=searxng_results,
                suggestions=searxng_response.suggestions,
                provider=searxng_response.provider,
                query=query,
            )

        if quality_triggered:
            self._logger.info(
                "SearxNG results quality below threshold, merging Google fallback",
                extra={
                    "query": query,
                    "quality_score": searxng_ranked.quality_score,
                    "quality_threshold": self._min_quality_score,
                    "fallback_reason": "quality_below_threshold",
                },
            )
        else:
            self._logger.info(
                "SearxNG returned no results, falling back to Google",
                extra={
                    "query": query,
                    "provider": "google",
                    "fallback_reason": "searxng_empty",
                },
            )

        google_response = await self._google.search(query, category, limit)
        merged = merge_ranked_results(query, searxng_results, google_response.results, limit)

        if not merged.results:
            self._logger.info(
                "Fallback search returned no results",
                extra={
                    "query": query,
                    "fallback_reason": "google_failed",
                },
            )
            return SearchResponse(
                results=[],
                suggestions=searxng_response.suggestions,
                provider=google_response.provider,
                query=query,
            )

        provider_name = google_response.provider
        if searxng_results and google_response.results:
            provider_name = "searxng+google"
        elif searxng_results:
            provider_name = searxng_response.provider

        self._logger.info(
            "Fallback search completed",
            extra={
                "query": query,
                "provider": provider_name,
                "results_count": len(merged.results),
                "quality_score": merged.quality_score,
            },
        )

        return SearchResponse(
            results=merged.results,
            suggestions=searxng_response.suggestions,
            provider=provider_name,
            query=query,
        )

    async def get_suggestions(self, query: str) -> list[str]:
        await self._rate_limiter.acquire()

        suggestions = await self._searxng.get_suggestions(query)

        if suggestions:
            return suggestions

        if self._fallback_enabled:
            return await self._google.get_suggestions(query)

        return []

    async def close(self) -> None:
        await self._searxng.close()
