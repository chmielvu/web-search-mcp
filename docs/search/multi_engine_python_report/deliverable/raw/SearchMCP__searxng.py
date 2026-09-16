from typing import Any

import httpx

from web_mcp.config import settings
from web_mcp.search.base import VALID_CATEGORIES, SearchProvider, SearchResponse, SearchResult
from web_mcp.search.relevance import clean_search_snippet, select_engines_for_query
from web_mcp.utils.logger import get_logger


class SearxNGProvider(SearchProvider):
    def __init__(self, base_url: str | None = None, timeout: int | None = None):
        resolved_base_url = settings.SEARXNG_URL if base_url is None else base_url
        self._base_url = resolved_base_url.rstrip("/")
        self._timeout = timeout or settings.SEARXNG_TIMEOUT
        self._logger = get_logger("web_mcp")
        self._client: httpx.AsyncClient | None = None

    @property
    def name(self) -> str:
        return "searxng"

    @property
    def is_available(self) -> bool:
        return bool(self._base_url)

    def _get_client(self) -> httpx.AsyncClient:
        """Return a reusable HTTP client (creates one if needed).

        Reusing the client across requests enables HTTP connection pooling,
        which is faster than creating a new connection for every search.
        """
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def _validate_category(self, category: str) -> str:
        if category not in VALID_CATEGORIES:
            self._logger.warning(
                f"Invalid category '{category}', falling back to 'general'",
                extra={"category": category, "valid_categories": list(VALID_CATEGORIES)},
            )
            return "general"
        return category

    def _parse_result(self, item: dict[str, Any]) -> SearchResult:
        raw_description = str(item.get("content", ""))
        return SearchResult(
            title=item.get("title", ""),
            url=item.get("url", ""),
            description=clean_search_snippet(raw_description),
            source=item.get("engine", ""),
        )

    @staticmethod
    def _parse_suggestions(data: dict[str, Any]) -> list[str]:
        """Extract query suggestions from a SearxNG JSON response.

        SearxNG may return suggestions under "suggestions" or "corrections" key.
        """
        suggestions = data.get("suggestions", []) or data.get("corrections", [])
        if isinstance(suggestions, list):
            return [str(s) for s in suggestions if s]
        return []

    def _log_request_error(self, e: Exception, url: str, context: str) -> None:
        """Log a SearxNG request error with appropriate detail based on exception type.

        Args:
            e: The caught exception
            url: The SearxNG URL that was called
            context: Human-readable label like "search" or "suggestions"
        """
        if isinstance(e, httpx.TimeoutException):
            self._logger.error(
                f"SearxNG {context} timed out: {e}",
                extra={"url": url, "timeout": self._timeout, "error": str(e)},
            )
        elif isinstance(e, httpx.ConnectError):
            self._logger.error(
                f"Failed to connect to SearxNG: {e}",
                extra={"url": url, "error": str(e)},
            )
        elif isinstance(e, httpx.HTTPStatusError):
            self._logger.error(
                f"SearxNG HTTP error: {e.response.status_code}",
                extra={"url": url, "status_code": e.response.status_code, "error": str(e)},
            )
        elif isinstance(e, httpx.HTTPError):
            self._logger.error(
                f"SearxNG HTTP error: {e}",
                extra={"url": url, "error": str(e)},
            )
        elif isinstance(e, (KeyError, TypeError, ValueError)):
            self._logger.error(
                f"Failed to parse SearxNG {context}: {e}",
                extra={"error": str(e)},
            )
        else:
            self._logger.error(
                f"Unexpected SearxNG {context} error: {e}",
                extra={"url": url, "error": str(e)},
            )

    def _parse_response(self, data: dict[str, Any], query: str, limit: int = 0) -> SearchResponse:
        results_data = data.get("results", [])
        suggestions = self._parse_suggestions(data)

        results = [self._parse_result(item) for item in results_data if item.get("url")]

        if limit > 0:
            results = results[:limit]

        return SearchResponse(
            results=results,
            suggestions=suggestions,
            provider=self.name,
            query=query,
        )

    def _resolve_candidate_limit(self, limit: int) -> int:
        """Calculate how many candidates to fetch from SearxNG before reranking.

        We fetch more results than the user asked for (e.g., 5x) so our
        relevance ranker in relevance.py has a larger pool to pick the best
        results from. The final output is trimmed to the user's requested limit.
        """
        if limit <= 0:
            return settings.SEARCH_MAX_CANDIDATES

        multiplier = max(1, settings.SEARCH_CANDIDATE_MULTIPLIER)
        expanded = limit * multiplier
        bounded = min(settings.SEARCH_MAX_CANDIDATES, expanded)
        return max(limit, bounded)

    async def search(self, query: str, category: str = "general", limit: int = 5) -> SearchResponse:
        category = self._validate_category(category)
        requested_limit = max(1, limit)
        candidate_limit = self._resolve_candidate_limit(requested_limit)

        params: dict[str, Any] = {
            "format": "json",
            "q": query,
            "categories": category,
        }

        selected_engines: list[str] | None = None
        if category == "general":
            selected_engines = select_engines_for_query(
                query=query,
                mode=settings.SEARCH_ENGINE_PROFILE_MODE,
                security_engines_raw=settings.SEARCH_SECURITY_ENGINES,
                general_engines_raw=settings.SEARCH_GENERAL_ENGINES,
            )
            if selected_engines:
                params["engines"] = ",".join(selected_engines)

        url = f"{self._base_url}/search"

        self._logger.debug(
            f"Searching SearxNG: {query}",
            extra={
                "url": url,
                "category": category,
                "limit": requested_limit,
                "candidate_limit": candidate_limit,
                "engines": selected_engines,
            },
        )

        try:
            client = self._get_client()
            response = await client.get(url, params=params)
            response.raise_for_status()

            data = response.json()

            if not isinstance(data, dict):
                self._logger.error(
                    "Invalid response format from SearxNG",
                    extra={"response_type": type(data).__name__},
                )
                return SearchResponse(
                    results=[],
                    suggestions=[],
                    provider=self.name,
                    query=query,
                )

            search_response = self._parse_response(data, query, candidate_limit)

            self._logger.info(
                f"SearxNG search completed: {len(search_response.results)} results",
                extra={
                    "query": query,
                    "category": category,
                    "results_count": len(search_response.results),
                    "candidate_limit": candidate_limit,
                    "engines": selected_engines,
                },
            )

            return search_response

        except Exception as e:
            self._log_request_error(e, url, "search")
            return SearchResponse(
                results=[],
                suggestions=[],
                provider=self.name,
                query=query,
            )

    async def get_suggestions(self, query: str) -> list[str]:
        params: dict[str, Any] = {
            "format": "json",
            "q": query,
        }

        url = f"{self._base_url}/search"

        self._logger.debug(f"Getting suggestions from SearxNG: {query}", extra={"url": url})

        try:
            client = self._get_client()
            response = await client.get(url, params=params)
            response.raise_for_status()

            data = response.json()

            if not isinstance(data, dict):
                self._logger.error(
                    "Invalid response format from SearxNG",
                    extra={"response_type": type(data).__name__},
                )
                return []

            suggestions = self._parse_suggestions(data)

            self._logger.debug(
                f"SearxNG suggestions: {len(suggestions)} found",
                extra={"query": query, "suggestions_count": len(suggestions)},
            )

            return suggestions

        except Exception as e:
            self._log_request_error(e, url, "suggestions")
            return []
